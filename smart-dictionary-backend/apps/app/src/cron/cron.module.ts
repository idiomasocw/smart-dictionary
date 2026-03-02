import { Module } from '@nestjs/common';
import { BullModule } from '@nestjs/bullmq';
import { ConfigModule } from '@nestjs/config';
import { CronService } from './cron.service';

@Module({
    imports: [
        ConfigModule,
        BullModule.registerQueue({
            name: 'dictionary-jobs',
        }),
    ],
    providers: [CronService],
})
export class CronModule { }
