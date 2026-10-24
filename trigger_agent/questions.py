"""Keep generated questions short enough to answer at a glance."""

MAX_QUESTION_LENGTH = 72
MAX_QUESTION_WORDS = 12
JARGON = ('along the current direction', 'expand more fully', 'adjust the current focus', 'degree of elaboration', 'contract the scope',
          'positive feedback', 'negative feedback', 'adjustment dimension', 'execution boundaries', 'adopt the current', 'exclude the current',
          'mainline system', 'side-branch question')


def validate_question(question: str) -> str:
    if not isinstance(question, str):
        raise ValueError('The question must be a single sentence.')
    question = question.strip()
    if (not question or len(question) > MAX_QUESTION_LENGTH or "\n" in question
            or len(question.split()) > MAX_QUESTION_WORDS):
        raise ValueError('Ask one short question, at most 72 characters. For example: "Is it for you?"')
    if any(word in question.casefold() for word in JARGON):
        raise ValueError('Ask directly what the user wants. For example: "Is it for other people?" Avoid abstract jargon.')
    if not question.endswith(("?", "？")) or question.count("?") + question.count("？") != 1:
        raise ValueError('Ask one yes/no question at a time. For example: "Will you post it online?"')
    return question
