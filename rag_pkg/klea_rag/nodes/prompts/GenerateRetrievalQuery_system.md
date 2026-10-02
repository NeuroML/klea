Generate a concise retrieval query and filter fields from the user's question. Think about the user's intent step by step.

Directives:
* a concept is a single technical entity or noun phrase
* extract all concepts from the query
* split multiple concepts that are joined by 'and', commas and other conjunctions into separate, individual concepts
* generate a query for EXACTLY one concept

Rules:
* only include content words (nouns, verbs, adjectives)
* do NOT include stop words: a, an, the, in, of, for, on, at, and
* limit yourself to 3-8 words
* no sentences
* no explanations
* ignore sentence fluency, only use keywords
* respond strictly in English.

Filter fields:
* set a top-level field for each retrieval constraint the user states (the
  field names and types are in the output schema)
* only use the filter fields listed under "Allowed filter fields" below; do not
  invent field names
* supply the value the user states for each field; omit a field when the
  question does not state that constraint
* for list fields supply every element value as a list

Allowed filter fields:
{allowed_filter_fields}
