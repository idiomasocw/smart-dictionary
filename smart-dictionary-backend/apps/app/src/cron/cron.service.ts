import { Injectable, Logger } from '@nestjs/common';
import { Cron, CronExpression } from '@nestjs/schedule';
import { InjectQueue } from '@nestjs/bullmq';
import { Queue } from 'bullmq';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class CronService {
    private readonly logger = new Logger(CronService.name);
    private supabase: SupabaseClient;

    constructor(
        @InjectQueue('dictionary-jobs') private readonly dictionaryQueue: Queue,
        private configService: ConfigService,
    ) {
        this.supabase = createClient(
            this.configService.get<string>('SUPABASE_URL') || process.env.SUPABASE_URL || 'http://localhost:56321',
            this.configService.get<string>('SUPABASE_SECRET_KEY') || process.env.SUPABASE_SECRET_KEY || 'mock-secret',
        );
    }

    // Polls every 7 minutes for pending words
    @Cron('0 */7 * * * *')
    async dispatchPendingWords() {
        this.logger.debug('Polling for PENDING dictionary seeds...');

        // 1. Query for up to 20 PENDING words
        const { data: words, error } = await this.supabase
            .from('words')
            .select('id, lemma, language_code')
            .eq('status', 'PENDING')
            .limit(20);

        if (error) {
            this.logger.error('Error fetching pending words', error);
            return;
        }

        if (!words || words.length === 0) {
            this.logger.debug('No pending words found.');
            return;
        }

        // 2. Mark them as PROCESSING
        const wordIds = words.map(w => w.id);
        await this.supabase
            .from('words')
            .update({ status: 'PROCESSING', updated_at: new Date().toISOString() })
            .in('id', wordIds);

        // 3. Dispatch to BullMQ
        for (const word of words) {
            await this.dictionaryQueue.add('generate-dictionary-entry', {
                wordId: word.id,
                lemma: word.lemma,
                targetLanguage: word.language_code
            });
            this.logger.log(`Dispatched lexicographer job for word: ${word.lemma}`);
        }
    }
}
