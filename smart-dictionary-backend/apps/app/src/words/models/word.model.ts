import { Field, ID, ObjectType } from '@nestjs/graphql';
import { Definition } from './definition.model';

@ObjectType()
export class Word {
    @Field(() => ID)
    id: string;

    @Field()
    lemma: string;

    @Field({ nullable: true })
    audioS3Url?: string;

    @Field(() => [Definition])
    definitions: Definition[];
}
