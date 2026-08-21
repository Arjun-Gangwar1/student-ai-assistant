"""
RAG answer generator.
Retrieves relevant context and generates a grounded, source-cited response.
"""

import logging
from app.intelligence.llm_client import llm
from app.rag.retriever import retrieve, format_context_for_llm

logger = logging.getLogger(__name__)

RAG_SYSTEM = """You are a helpful AI assistant for IIT Dharwad students.
Your job is to help students stay on top of their academic life.

Rules:
- Answer ONLY using the provided context. Never make up information.
- If the answer is not in the context, say "I don't have that information right now."
- Be concise and direct. Max 3-4 sentences unless more detail is needed.
- Use Hinglish (mix of Hindi and English) only if the student's question is in Hindi/Hinglish.
- Always mention the source and date of information when relevant.
- For deadlines: always state how many days/hours are left.
- Never hallucinate deadlines or dates."""

RAG_PROMPT = """Context from your academic platforms (most relevant first):
{context}

Student's question: {question}

Answer:"""


async def answer_question(
    question: str,
    student_id: "str | list[str]",
    chat_history: list[dict] | None = None,
) -> dict:
    """
    Full RAG pipeline: retrieve → format context → generate answer.
    Returns dict with answer + source_items.
    """
    # Retrieve relevant items
    items = await retrieve(question, student_id, top_k=6)
    context = format_context_for_llm(items)

    # Build message history for multi-turn
    messages = [{"role": "system", "content": RAG_SYSTEM}]

    if chat_history:
        # Include last 4 turns for context
        messages.extend(chat_history[-8:])

    messages.append({
        "role": "user",
        "content": RAG_PROMPT.format(context=context, question=question),
    })

    try:
        answer = llm().chat(messages=messages, temperature=0.2)
    except Exception as e:
        logger.error(f"LLM generation failed: {e}")
        answer = "Sorry, I'm having trouble answering right now. Please try again."

    return {
        "answer": answer,
        "sources": [
            {
                "id": item["id"],
                "title": item.get("title", ""),
                "source": item.get("source", ""),
                "created_at": item.get("created_at", ""),
            }
            for item in items[:3]
        ],
    }
