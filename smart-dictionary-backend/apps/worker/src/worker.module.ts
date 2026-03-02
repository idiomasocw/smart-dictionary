import { Module } from '@nestjs/common';
import { ConfigModule, ConfigService } from '@nestjs/config';
import { BullModule } from '@nestjs/bullmq';
import { LexicographerProcessor } from './processors/lexicographer.processor';
import { VideoProcessor } from './processors/video.processor';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env'],
    }),
    BullModule.forRootAsync({
      imports: [ConfigModule],
      useFactory: async (configService: ConfigService) => ({
        connection: {
          host: configService.get('REDIS_HOST') || 'localhost',
          port: configService.get('REDIS_PORT') || 6379,
        },
      }),
      inject: [ConfigService],
    }),
    BullModule.registerQueue({
      name: 'dictionary-jobs',
    }),
    BullModule.registerQueue({
      name: 'video-jobs',
    }),
  ],
  providers: [LexicographerProcessor, VideoProcessor],
})
export class WorkerModule { }
