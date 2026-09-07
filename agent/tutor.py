"""
tutor.py — Study tutor persona, system prompt, and quiz logic.

Provides the system prompt and conversation management for
VoiceForge StudyBuddy — a voice-first study tutor with active
interruption handling and recovery.
"""

import asyncio
import logging
import random
from typing import Optional, Callable, Dict, List

logger = logging.getLogger("voiceforge.tutor")

# ─── System Prompt ───────────────────────────────────────────────────────────
# Written for the ear: short sentences, natural rhythm, conversational tone.
# Incorporates natural interruption & recovery behaviors.

SYSTEM_PROMPT = """You are StudyBuddy, a friendly and encouraging voice-first study tutor.
You help students learn through spoken conversation — they listen with their ears, not read with their eyes.

RULES FOR SPOKEN OUTPUT (critical — you are being read aloud by a TTS engine):
- Keep sentences SHORT. Max 15 words per sentence. Break long explanations into multiple short sentences.
- Use natural, conversational language. Say "Let's" not "Let us". Say "gonna" occasionally.
- Never use bullet points, numbered lists, markdown, URLs, or special formatting — the student HEARS you, not reads you.
- Spell out abbreviations. Say "DNA" as "D-N-A". Say "e.g." as "for example".
- For numbers, use words: "three hundred" not "300".
- Pause between ideas. Use periods, not semicolons.
- After asking a question, STOP. Wait for the student to answer. Do not answer your own question.
- When giving feedback, be warm and specific. "Great job!" then explain WHY they're right.
- If they're wrong, be gentle. "Not quite — here's the thing..." then give the right answer with a brief explanation.

INTERRUPTION & RECOVERY RULES (CRITICAL):
- The student may interrupt you at any time. When they cut in with a new question or change the subject, pivot instantly and gracefully.
- Acknowledge interruptions naturally when appropriate: "Got it!", "No problem, let's switch gears!", or "Sure thing!"
- NEVER try to finish your old thought or answer from before the interruption.
- Never complain about being interrupted. Treat interruptions as normal, dynamic human dialogue.
- If the student interrupts during a lookup or explanation, immediately focus on their newest request.

QUIZ FLOW:
1. Greet the student warmly. Ask what subject they want to study.
2. Once they pick a topic, ask quiz questions one at a time.
3. Wait for their spoken answer.
4. Give feedback: correct or incorrect, with a brief explanation.
5. Then move to the next question.
6. Every 3-4 questions, offer encouragement. "You're doing awesome! Ready for the next one?"
7. If they say "explain more" or "I don't understand", give a longer but still spoken-friendly explanation.

LOOKUP TOOL:
- If the student asks you to "look something up", "search for", or "find information about" a specific topic, 
  you must use the lookup_topic tool. While waiting for the result, briefly say something like 
  "Let me look that up for you" or "Good question, give me a moment to find that."
- If the student interrupts while you are looking something up, discard that lookup and respond to their new statement.

PERSONALITY:
- Warm, patient, encouraging
- Slightly playful — occasional "Ooh, tough one!" or "You're on fire!"
- Celebrates effort, not just correctness
"""


# ─── Flashcard Data (demo set) ──────────────────────────────────────────────

DEMO_FLASHCARDS = {
    "biology": [
        {
            "question": "What organelle is known as the powerhouse of the cell?",
            "answer": "mitochondria",
            "explanation": "Mitochondria produce most of the cell's energy through a process called cellular respiration. They convert nutrients into A-T-P, which is the energy currency of the cell.",
        },
        {
            "question": "What is the process by which plants convert sunlight into food called?",
            "answer": "photosynthesis",
            "explanation": "Photosynthesis happens mainly in the leaves. Plants use sunlight, water, and carbon dioxide to make glucose and oxygen.",
        },
        {
            "question": "What molecule carries genetic instructions in living organisms?",
            "answer": "DNA",
            "explanation": "D-N-A stands for deoxyribonucleic acid. It's shaped like a twisted ladder, called a double helix. It contains the instructions for building proteins.",
        },
        {
            "question": "What type of cell division produces two identical daughter cells?",
            "answer": "mitosis",
            "explanation": "Mitosis is how your body grows and repairs itself. Each daughter cell gets an exact copy of the parent cell's D-N-A.",
        },
        {
            "question": "What is the largest organ in the human body?",
            "answer": "skin",
            "explanation": "Your skin covers about twenty square feet. It protects you from germs, regulates temperature, and lets you feel touch.",
        },
    ],
    "history": [
        {
            "question": "In what year did World War Two end?",
            "answer": "1945",
            "explanation": "World War Two ended in nineteen forty-five. Germany surrendered in May, and Japan surrendered in August after the atomic bombings.",
        },
        {
            "question": "Who was the first president of the United States?",
            "answer": "George Washington",
            "explanation": "George Washington served as president from seventeen eighty-nine to seventeen ninety-seven. He's often called the Father of His Country.",
        },
        {
            "question": "What ancient civilization built the pyramids at Giza?",
            "answer": "ancient Egyptians",
            "explanation": "The Great Pyramid of Giza was built around twenty-five hundred B-C. It was the tallest structure in the world for over three thousand years.",
        },
        {
            "question": "What event is considered the start of the French Revolution?",
            "answer": "storming of the Bastille",
            "explanation": "The Bastille was a fortress and prison in Paris. On July fourteenth, seventeen eighty-nine, revolutionaries stormed it. That date is now France's national holiday.",
        },
    ],
    "science": [
        {
            "question": "What is the chemical symbol for water?",
            "answer": "H2O",
            "explanation": "Water is made of two hydrogen atoms and one oxygen atom. We write it as H-two-O.",
        },
        {
            "question": "What force keeps planets in orbit around the sun?",
            "answer": "gravity",
            "explanation": "Gravity is the force of attraction between objects with mass. The sun's gravity keeps all the planets in our solar system in orbit.",
        },
        {
            "question": "What is the speed of light in a vacuum, approximately?",
            "answer": "three hundred thousand kilometers per second",
            "explanation": "Light travels at about three hundred thousand kilometers per second. That's fast enough to go around the Earth seven and a half times in one second.",
        },
    ],
}


def get_flashcards_for_topic(topic: str) -> list[dict]:
    """Get flashcards for a given topic, with fuzzy matching."""
    topic_lower = topic.lower().strip()
    for key, cards in DEMO_FLASHCARDS.items():
        if key in topic_lower or topic_lower in key:
            return cards
    return random.choice(list(DEMO_FLASHCARDS.values()))


# ─── Slow Tool Simulation with Interruption Cancellation ────────────────────

async def simulate_slow_lookup(
    topic: str,
    check_cancelled: Optional[Callable[[], bool]] = None,
    delay_seconds: Optional[float] = None,
) -> str:
    """
    Simulate a slow external lookup (3.0-4.5s) with cooperative cancellation.
    Periodically checks if the current generation was cancelled due to user barge-in.
    """
    total_delay = delay_seconds if delay_seconds is not None else random.uniform(3.0, 4.5)
    logger.info("simulate_slow_lookup: topic='%s', planned delay=%.1fs", topic, total_delay)

    # Sleep in small slices (100ms) to check cancellation promptly
    interval = 0.1
    elapsed = 0.0
    while elapsed < total_delay:
        if check_cancelled and check_cancelled():
            logger.info(
                "🛑 simulate_slow_lookup: cancelled after %.2fs for topic '%s' due to user interruption",
                elapsed,
                topic,
            )
            return f"[CANCELLED] Lookup for {topic} was cancelled because you interrupted."
        await asyncio.sleep(interval)
        elapsed += interval

    # Return curated spoken-friendly responses
    responses = {
        "quantum": (
            "Quantum mechanics explores the strange behavior of particles at atomic scales. "
            "Particles can exist in multiple states at once until measured."
        ),
        "black holes": (
            "A black hole has gravity so strong that even light cannot escape. "
            "They usually form when massive stars collapse at the end of their lives."
        ),
        "evolution": (
            "Evolution is the genetic change in populations over generations. "
            "Charles Darwin showed how natural selection drives adaptation to environments."
        ),
        "crispr": (
            "Crispr is a gene editing technology inspired by bacterial immune systems. "
            "It lets scientists cut and modify D-N-A sequences with high precision."
        ),
        "relativity": (
            "Albert Einstein's theory of relativity showed that space and time are intertwined. "
            "Massive objects actually warp the fabric of spacetime around them."
        ),
    }

    for key, response in responses.items():
        if key in topic.lower():
            return response

    return (
        f"Here is what I found about {topic}. "
        "It has significant historical impact and remains an active subject of modern research. "
        "Key concepts connect directly to core scientific principles."
    )
