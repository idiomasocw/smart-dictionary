import { Field, ID, ObjectType } from '@nestjs/graphql';
import { TextExample } from './text-example.model';

@ObjectType()
export class Definition {
    @Field(() => ID)
    id: string;

    @Field()
    partOfSpeech: string;

    @Field({ nullable: true })
    cefrLevel?: string;

    @Field()
    meaning: string;

    @Field(() => String, { nullable: true })
    translation?: string;

    @Field(() => [TextExample])
    textExamples: TextExample[];
}
