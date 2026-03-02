import { Processor, WorkerHost } from '@nestjs/bullmq';
import { Job } from 'bullmq';
import { Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { createClient, SupabaseClient } from '@supabase/supabase-js';
import { createClient as createDeepgramClient, DeepgramClient } from '@deepgram/sdk';
import { GoogleGenAI } from '@google/genai';
import ffmpeg = require('fluent-ffmpeg');
import ffmpegInstaller = require('@ffmpeg-installer/ffmpeg');
import * as fs from 'fs';
import * as path from 'path';
import * as os from 'os';

// Configure fluent-ffmpeg to use the installed binary
ffmpeg.setFfmpegPath(ffmpegInstaller.path);

@Processor('video-jobs')
export class VideoProcessor extends WorkerHost {
    private readonly logger = new Logger(VideoProcessor.name);
    private supabase: SupabaseClient;
    private deepgram: DeepgramClient;
    private ai: GoogleGenAI;

    constructor(private configService: ConfigService) {
        super();
        this.supabase = createClient(
            this.configService.get<string>('SUPABASE_URL') || process.env.SUPABASE_URL || 'http://localhost:56321',
            this.configService.get<string>('SUPABASE_SECRET_KEY') || process.env.SUPABASE_SECRET_KEY || 'mock-secret',
        );
        this.deepgram = createDeepgramClient(this.configService.get<string>('DEEPGRAM_API_KEY') || 'mock');
        this.ai = new GoogleGenAI({ apiKey: this.configService.get<string>('GEMINI_API_KEY') || 'mock' });
    }

    async process(job: Job<any, any, string>): Promise<any> {
        this.logger.debug(`Processing video job ${job.id} for video: ${job.data.videoId}`);
        const { videoId, videoS3Url, languageCode } = job.data;

        const tempDir = os.tmpdir();
        const audioPath = path.join(tempDir, `${videoId}.wav`);

        try {
            // 1. Download video or stream it directly using FFmpeg to extract audio
            await this.extractAudio(videoS3Url, audioPath);

            // 2. Transcribe Audio via Deepgram
            const transcription = await this.transcribeAudio(audioPath, languageCode);

            // 3. Process Transcription to Find Phrases (Linguistic Director Logic)
            const matches = await this.findMatchingDefinitions(transcription, languageCode);

            // 4. Clip Video for Matches
            for (const match of matches) {
                // Logic to extract video clips
                const clipPath = path.join(tempDir, `clip_${videoId}_${match.start}.mp4`);
                await this.clipVideo(videoS3Url, clipPath, match.start, match.end);

                // Logic to upload clipPath to S3 and save to DB
                await this.saveClipToDatabase(videoId, match, clipPath);
            }

            await this.supabase.from('source_videos').update({ status: 'READY' }).eq('id', videoId);
            this.logger.log(`Completed video enrichment for ${videoId}`);
        } catch (error) {
            this.logger.error(`Failed video processing for ${videoId}:`, error);
            await this.supabase.from('source_videos').update({ status: 'FAILED' }).eq('id', videoId);
            throw error;
        } finally {
            // Cleanup
            if (fs.existsSync(audioPath)) fs.unlinkSync(audioPath);
        }
    }

    private extractAudio(videoUrl: string, outputPath: string): Promise<void> {
        return new Promise((resolve, reject) => {
            this.logger.debug(`Extracting audio to ${outputPath}`);
            ffmpeg(videoUrl)
                .noVideo()
                .audioCodec('pcm_s16le')
                .audioChannels(1)
                .audioFrequency(16000)
                .on('end', () => resolve())
                .on('error', (err: any) => reject(err))
                .save(outputPath);
        });
    }

    private async transcribeAudio(audioPath: string, language: string): Promise<any> {
        this.logger.debug('Sending to Deepgram for transcription');
        const audioContent = fs.readFileSync(audioPath);

        // Use language model mapping if needed, falling back to base language code
        const dgResponse = await this.deepgram.listen.prerecorded.transcribeFile(
            audioContent,
            {
                model: 'nova-2',
                language: language,
                smart_format: true,
                filler_words: false, // We usually want clean words for dict matching
                punctuate: true,
                paragraphs: true,
            }
        );

        // The structure depends on Deepgram SDK, typically: dgResponse.result.results.channels[0].alternatives[0]
        return dgResponse.result?.results?.channels[0]?.alternatives[0];
    }

    private async findMatchingDefinitions(transcription: any, language: string): Promise<Array<{ word_id: string, def_id: string, start: number, end: number }>> {
        // 1. Logic to take Deepgram output and identify words
        // 2. Query Supabase for those words
        // 3. Optional: Use Gemini to verify context if word is polysemous
        // This is a placeholder for the actual complex chunking logic
        this.logger.debug(`Found ${transcription?.words?.length || 0} words in transcription`);
        return []; // Return mocked standard matched object: { word_id, def_id, start, end }
    }

    private clipVideo(videoUrl: string, outputPath: string, startParam: number, endParam: number): Promise<void> {
        return new Promise((resolve, reject) => {
            // Fast seek using input seeking (-ss before -i), requires duration (-t)
            const duration = endParam - startParam;

            ffmpeg()
                .inputOptions([`-ss ${startParam}`])
                .input(videoUrl)
                .outputOptions([`-t ${duration}`, '-c copy', '-avoid_negative_ts make_zero'])
                .on('end', () => resolve())
                .on('error', (err: any) => reject(err))
                .save(outputPath);
        });
    }

    private async saveClipToDatabase(videoId: string, matchData: any, localClipPath: string) {
        // Upload clip back to Supabase Storage / S3
        // Insert row into video_clips
        this.logger.debug(`Simulated saving clip for ${matchData}`);
    }
}
