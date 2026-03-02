import { Processor, WorkerHost } from '@nestjs/bullmq';
import { Job } from 'bullmq';
import { Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { GoogleGenAI, Type, Schema } from '@google/genai';
import { createClient, SupabaseClient } from '@supabase/supabase-js';

@Processor('dictionary-jobs')
export class LexicographerProcessor extends WorkerHost {
    private readonly logger = new Logger(LexicographerProcessor.name);
    private ai: GoogleGenAI;
    private supabase: SupabaseClient;

    // The Expected JSON schema enforced onto Gemini
    private dictionarySchema: Schema = {
        type: Type.OBJECT,
        properties: {
            lemma: { type: Type.STRING },
            targetLanguage: { type: Type.STRING },
            conjugations: {
                type: Type.ARRAY,
                items: { type: Type.STRING },
            },
            definitions: {
                type: Type.ARRAY,
                items: {
                    type: Type.OBJECT,
                    properties: {
                        partOfSpeech: { type: Type.STRING },
                        cefrLevel: { type: Type.STRING },
                        meaning: { type: Type.STRING },
                        translations: {
                            // Dynamic keys: "en": "Bank", "es": "banco", etc
                            type: Type.OBJECT,
                            properties: {
                                "en": { type: Type.STRING },
                                "es": { type: Type.STRING },
                                "fr": { type: Type.STRING },
                                "de": { type: Type.STRING },
                                "it": { type: Type.STRING },
                                "pt": { type: Type.STRING },
                            }
                        },
                        textExamples: {
                            type: Type.ARRAY,
                            items: {
                                type: Type.OBJECT,
                                properties: {
                                    exampleText: { type: Type.STRING },
                                    translations: {
                                        type: Type.OBJECT,
                                        properties: {
                                            "en": { type: Type.STRING },
                                            "es": { type: Type.STRING },
                                            "fr": { type: Type.STRING },
                                            "de": { type: Type.STRING },
                                            "it": { type: Type.STRING },
                                            "pt": { type: Type.STRING },
                                        }
                                    },
                                },
                                required: ['exampleText', 'translations'],
                            },
                        },
                    },
                    required: ['partOfSpeech', 'meaning', 'translations', 'textExamples'],
                },
            },
        },
        required: ['lemma', 'targetLanguage', 'conjugations', 'definitions'],
    };

    private systemPrompt = `
    You are an expert, multilingual lexicographer and linguist. Your task is to generate a comprehensive dictionary entry for a given root word.
    
    1. Identify the primary meanings of the word (limit to the top 3-4 most common usages).
    2. For each meaning, provide the part of speech and CEFR level.
    3. The Definition Rule: You must write a clear, concise definition of the word IN THE TARGET LANGUAGE (the language of the lemma). Do not use English unless English is the target language. Use simplified vocabulary.
    4. The Translation Rule: Provide exact translations into requested Native Languages.
    5. The Example Rule: Generate two natural text examples in the Target Language, then translate them.
    6. Generate an array of all common morphological variations (conjugations, plurals) of the root word in the Target Language.
    
    Output strictly in the requested JSON format.
  `;

    constructor(private configService: ConfigService) {
        super();
        this.ai = new GoogleGenAI({ apiKey: this.configService.get<string>('GEMINI_API_KEY') || 'mock' });
        this.supabase = createClient(
            this.configService.get<string>('SUPABASE_URL') || process.env.SUPABASE_URL || 'http://localhost:56321',
            this.configService.get<string>('SUPABASE_SECRET_KEY') || process.env.SUPABASE_SECRET_KEY || 'mock-secret',
        );
    }

    async process(job: Job<any, any, string>): Promise<any> {
        this.logger.debug(`Processing dictionary job ${job.id} for word: ${job.data.lemma}`);
        const { wordId, lemma, targetLanguage } = job.data;

        const nativeLanguages = ['en', 'es', 'fr', 'de', 'pt', 'it'].filter(l => l !== targetLanguage).join(', ');

        const userPrompt = `Generate the dictionary entry for the lemma: "${lemma}". Target Language: "${targetLanguage}". Translate into Native Languages: ${nativeLanguages}.`;

        try {
            const response = await this.ai.models.generateContent({
                model: 'gemini-2.5-flash',
                contents: [userPrompt],
                config: {
                    systemInstruction: this.systemPrompt,
                    responseMimeType: 'application/json',
                    responseSchema: this.dictionarySchema,
                    temperature: 0.1,
                },
            });

            const rawJson = response.text;
            if (!rawJson) throw new Error("Empty response from Gemini");

            const parsedData = JSON.parse(rawJson);

            // Save Data to Supabase Transactionally
            await this.saveDictionaryEntryToSupabase(wordId, parsedData);

            this.logger.log(`Successfully completed dictionary job ${job.id} for word: ${lemma}`);
            return parsedData;

        } catch (error) {
            this.logger.error(`Failed to process dictionary entry for ${lemma}:`, error);

            await this.supabase
                .from('words')
                .update({ status: 'FAILED' })
                .eq('id', wordId);

            throw error;
        }
    }

    private async saveDictionaryEntryToSupabase(wordId: string, data: any) {
        // 1. Update Core Word
        const { error: wordErr } = await this.supabase
            .from('words')
            .update({
                conjugations: data.conjugations,
                status: 'COMPLETED'
            })
            .eq('id', wordId);

        if (wordErr) throw wordErr;

        // 2. Insert Definitions
        for (const def of data.definitions) {
            const { data: insertedDef, error: defErr } = await this.supabase
                .from('definitions')
                .insert({
                    word_id: wordId,
                    part_of_speech: def.partOfSpeech,
                    cefr_level: def.cefrLevel,
                    meaning: def.meaning,
                    translations: def.translations
                })
                .select('id')
                .single();

            if (defErr) {
                this.logger.error(`Failed to insert definition: ${defErr.message}`);
                continue; // Move to next one to avoid full crashing if AI hallucinated dupes
            }

            // 3. Insert Examples Arrays
            if (def.textExamples && def.textExamples.length > 0) {
                const exampleInserts = def.textExamples.map((ex: any) => ({
                    definition_id: insertedDef.id,
                    example_text: ex.exampleText,
                    translations: ex.translations
                }));

                await this.supabase.from('text_examples').insert(exampleInserts);
            }
        }
    }
}
