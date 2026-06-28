from typing import List, Dict, Any

def search_rag_chunks(query_keywords: List[str], rag_chunks: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """
    Basic keyword search over RAG chunks.
    Matches keywords against title, keywords, and chunk_text.
    """
    results = []
    for chunk in rag_chunks:
        if not chunk.get("is_active", True):
            continue
            
        score = 0
        text_content = (
            str(chunk.get("title", "")) + " " +
            str(chunk.get("keywords", "")) + " " +
            str(chunk.get("chunk_text", ""))
        ).lower()
        
        for keyword in query_keywords:
            if keyword.lower() in text_content:
                score += 1
                
        if score > 0:
            results.append({"chunk": chunk, "score": score})
            
    # Sort by score descending
    results.sort(key=lambda x: x["score"], reverse=True)
    return [r["chunk"] for r in results[:limit]]

def search_domestic_recall(keywords: List[str], recalls: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """
    Search domestic recall cases.
    """
    results = []
    for case in recalls:
        score = 0
        text_content = (
            str(case.get("product_name", "")) + " " +
            str(case.get("recall_reason", "")) + " " +
            str(case.get("hazard_content", ""))
        ).lower()
        
        for keyword in keywords:
            if keyword.lower() in text_content:
                score += 1
                
        if score > 0:
            results.append({"case": case, "score": score})
            
    results.sort(key=lambda x: x["score"], reverse=True)
    return [r["case"] for r in results[:limit]]
