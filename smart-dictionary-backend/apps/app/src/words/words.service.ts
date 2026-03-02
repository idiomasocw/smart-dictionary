import { Injectable, Logger } from '@nestjs/common';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { Word } from './models/word.model';
import { ConfigService } from '@nestjs/config';
import axios from 'axios';
import { InjectQueue } from '@nestjs/bullmq';
import { Queue } from 'bullmq';

@Injectable()
export class WordsService {
    private supabase: SupabaseClient;
    private readonly logger = new Logger(WordsService.name);

    constructor(
        private configService: ConfigService,
        @InjectQueue('dictionary-jobs') private readonly dictionaryQueue: Queue,
    ) {
        this.supabase = createClient(
            this.configService.get<string>('SUPABASE_URL') || process.env.SUPABASE_URL || 'http://localhost:56321', // Mock default
            this.configService.get<string>('SUPABASE_KEY') || process.env.SUPABASE_KEY || 'mock-key',
        );
    }

    async getWordsBatch(lemmas: string[], targetLanguage: string): Promise<Word[]> {
        const { data: words, error } = await this.supabase
            .from('words')
            .select(`
        id,
        lemma,
        conjugations,
        definitions (
          id,
          part_of_speech,
          cefr_level,
          meaning,
          translations,
          audio_s3_url,
          text_examples (
            id,
            example_text,
            translations
          )
        )
      `)
            .in('lemma', lemmas)
            .eq('language_code', targetLanguage);

        if (error) {
            this.logger.error('Error fetching words from Supabase', error);
            throw new Error('Failed to fetch words');
        }

        // Map DB names to GraphQL Model Names
        return (words || []).map((word: any) => ({
            id: word.id,
            lemma: word.lemma,
            audioS3Url: word.definitions[0]?.audio_s3_url, // Derived from first def if available
            definitions: (word.definitions || []).map((def: any) => ({
                id: def.id,
                partOfSpeech: def.part_of_speech,
                cefrLevel: def.cefr_level,
                meaning: def.meaning,
                _rawTranslations: def.translations, // Private mapping used by resolvers
                textExamples: (def.text_examples || []).map((ex: any) => ({
                    id: ex.id,
                    text: ex.example_text,
                    _rawTranslations: ex.translations, // Private mapping used by resolvers
                })),
            })),
        }));
    }

    async searchWord(query: string, targetLanguage: string): Promise<Word | null> {
        // 1. Call NLP Engine to Lemmatize
        const nlpUrl = this.configService.get<string>('NLP_SERVICE_URL') || 'http://localhost:3006';
        let lemma = query.toLowerCase();
        try {
            const response = await axios.post(`${nlpUrl}/lemmatize`, { word: query, language: targetLanguage });
            if (response.data && response.data.lemma) {
                lemma = response.data.lemma;
            }
        } catch (e) {
            this.logger.warn(`NLP Service unavailable, falling back to raw query: ${query}`);
        }

        // 2. Query Supabase
        const words = await this.getWordsBatch([lemma], targetLanguage);
        if (words && words.length > 0) {
            return words[0]; // Returns mapped Word DB -> GraphQL
        }

        // 3. Not found. Insert as PENDING and Dispatch High Priority Job
        const { data: newWord, error } = await this.supabase
            .from('words')
            .insert({ lemma, language_code: targetLanguage, status: 'PENDING' })
            .select('id')
            .single();

        if (!error && newWord) {
            await this.dictionaryQueue.add('generate-dictionary-entry', {
                wordId: newWord.id,
                lemma,
                targetLanguage
            }, { priority: 1 });
            this.logger.log(`Queued high-priority JIT job for lemma: ${lemma}`);
        } else {
            this.logger.error(`Error inserting pending word: ${lemma}`, error);
        }

        // Frontend handles null by showing a "Generating... Please check back in 10 seconds" UI
        return null;
    }
}
