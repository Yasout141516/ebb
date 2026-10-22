You write quiz questions that test one concept.

Concept: $name
What understanding it means: $description

The questions between the markers below cited their sources wrongly. Rewrite each of
them, in the same order, and return exactly $count. Keep each question's type ("mcq" or
"short"), difficulty and what it asks; change its wording only where the sources need
it, and cite the sources given below correctly.
<<<QUESTIONS
$questions
QUESTIONS>>>

Multiple choice: exactly 4 options and exactly one is correct. For every option,
"why" is one sentence saying why that option is right or wrong. Wrong options should
be plausible misconceptions, not jokes. "reference_answer" repeats the correct
option's text.

Short answer: "options" is an empty list and "reference_answer" is a model answer
of one to three sentences.

Do not repeat or closely paraphrase these earlier questions:
$avoid
