// The backend's own copy, word for word (backend/tests/test_api_study.py checks they match).

/**
 * The notice a turn carries when the tutor couldn't grade an answer: its question comes
 * back, and the website offers Retry. The backend's EXHAUSTED_NOTICE.
 */
export const EXHAUSTED_NOTICE = "The tutor couldn't respond — your progress is saved. Try again in a minute.";

/** The notice a turn carries when the tutor switched to its backup model. The backend's FALLBACK_NOTICE. */
export const FALLBACK_NOTICE = "Switched to a backup model while the main one is busy.";

/** An unexpected failure, worth trying again. The backend's SOMETHING_WRONG. */
export const SOMETHING_WRONG = "Something went wrong — try again.";
