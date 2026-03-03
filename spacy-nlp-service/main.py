import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import spacy
from typing import Dict, Any

app = FastAPI(title="SpaCy NLP Lemmatization Service")

# Load spaCy models (ensure these are installed: python -m spacy download en_core_web_sm)
models: Dict[str, Any] = {}

def get_model(language: str):
    if language in models:
        return models[language]
    
    model_name_map = {
        "en": "en_core_web_sm",
        "es": "es_core_news_sm",
        "fr": "fr_core_news_sm",
        "de": "de_core_news_sm",
        "it": "it_core_news_sm",
        "pt": "pt_core_news_sm",
    }
    
    model_name = model_name_map.get(language)
    if not model_name:
        raise HTTPException(status_code=400, detail=f"Unsupported language code: {language}")
    
    try:
        models[language] = spacy.load(model_name)
        return models[language]
    except OSError:
        raise HTTPException(status_code=500, detail=f"Model {model_name} is not installed on the server.")

class LemmatizeRequest(BaseModel):
    word: str
    language: str

class LemmatizeResponse(BaseModel):
    original: str
    lemma: str
    language: str

@app.post("/lemmatize", response_model=LemmatizeResponse)
async def lemmatize_word(request: LemmatizeRequest):
    nlp = get_model(request.language.lower())
    
    # Process the word
    doc = nlp(request.word)
    
    # Typically we just take the first token's lemma if it's a single word
    if len(doc) == 0:
        raise HTTPException(status_code=400, detail="Empty word provided")
        
    # Iterate through all tokens to support collocations and phrasal verbs
    lemma = " ".join([token.lemma_.lower() for token in doc])
    
    return LemmatizeResponse(
        original=request.word,
        lemma=lemma,
        language=request.language
    )

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "3006"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
