import { Resolver, Query, Args, ResolveField, Parent } from '@nestjs/graphql';
import { Word } from './models/word.model';
import { Definition } from './models/definition.model';
import { TextExample } from './models/text-example.model';
import { WordsService } from './words.service';

@Resolver(() => Word)
export class WordsResolver {
    constructor(private wordsService: WordsService) { }

    @Query(() => [Word])
    async getWordsBatch(
        @Args('lemmas', { type: () => [String] }) lemmas: string[],
        @Args('targetLanguage') targetLanguage: string,
    ) {
        return this.wordsService.getWordsBatch(lemmas, targetLanguage);
    }

    @Query(() => Word, { nullable: true })
    async searchWord(
        @Args('query') query: string,
        @Args('targetLanguage') targetLanguage: string,
    ) {
        return this.wordsService.searchWord(query, targetLanguage);
    }
}

@Resolver(() => Definition)
export class DefinitionResolver {
    @ResolveField('translation', () => String, { nullable: true })
    getTranslation(
        @Parent() definition: any,
        @Args('nativeLanguage') nativeLanguage: string,
    ) {
        return definition._rawTranslations?.[nativeLanguage] || null;
    }
}

@Resolver(() => TextExample)
export class TextExampleResolver {
    @ResolveField('translation', () => String, { nullable: true })
    getTranslation(
        @Parent() textExample: any,
        @Args('nativeLanguage') nativeLanguage: string,
    ) {
        return textExample._rawTranslations?.[nativeLanguage] || null;
    }
}
