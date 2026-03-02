import { Field, ID, ObjectType } from '@nestjs/graphql';

@ObjectType()
export class TextExample {
    @Field(() => ID)
    id: string;

    @Field()
    text: string;

    // Custom translation resolved dynamically
    @Field(() => String, { nullable: true })
    translation?: string;
}
