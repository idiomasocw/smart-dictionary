import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { BullModule } from '@nestjs/bullmq';
import { WordsResolver, DefinitionResolver, TextExampleResolver } from './words.resolver';
import { WordsService } from './words.service';

@Module({
    imports: [
        ConfigModule,
        BullModule.registerQueue({
            name: 'dictionary-jobs',
        }),
    ],
    providers: [WordsResolver, DefinitionResolver, TextExampleResolver, WordsService],
})
export class WordsModule { }
