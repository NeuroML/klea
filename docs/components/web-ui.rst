Web interface
=============

Klea's web interface is a `NiceGUI <https://nicegui.io/>`_ frontend that
talks to the backend API.  It is started with ``klea-rag web`` (RAG) or
``klea web`` (agent), and is the interface used by the hosted prototypes
linked from the :doc:`home page <../index>`.

The layout has three parts: a left drawer listing chat sessions, a centre
panel with two tabs (**chat** and **inspect**), and a right drawer with
per-chat state (the *status pane*).  A single footer line carries the
disclaimer and the "Powered by" note.

Chat
----

The chat tab is where questions are asked.  Answers stream in as they are
generated, and each answer is grounded in the retrieved sources rather
than the model's memory alone.  Messages render as full-width blocks: the
user's prompt is tinted, the agent's reply is plain text, and file changes
the agent makes appear as their own diff blocks before the reply.  Tool
results (file-edit diffs, command output) render as compact blocks
collapsed to a one-line preview that expands on click; an errored tool
block's title is shown in red.  Long lines wrap, and the input box sits at
the bottom (drag its corner to resize it).  Use the left drawer to start a
new chat or switch between existing ones, and the status pane to choose
the model for the chat.

Send stays disabled until the models a run needs (and any required API
keys) are configured; the first run opens a **Choose models** prompt to set
them.  If a run fails with a recoverable error, an inline **Retry** action
resumes it from its checkpoint -- re-running only the failed step -- instead
of starting the query over.

In the agent, a run can pause for your input.  The ask appears in a per-turn
region under the transcript (the *turn status* region, distinct from the
right-drawer status pane): a plan awaiting review shows the plan with
**Approve** / **Request changes** / **Cancel** controls, a plan blocked on a
missing fact shows one field per question, and a tool call that would touch a
path outside the project directory (or a sensitive file inside it, such as
``.env``) shows one **Once** / **Session** / **Deny** choice per path.  The
main input box is disabled until you answer or cancel, and reloading the page
re-shows a pending ask.  Answering (or requesting a revision) resumes the
same run with its state intact.

.. figure:: /_static/images/20260916-klea-rag-chat.png
   :alt: Klea RAG web interface chat tab showing a question and a cited answer
   :width: 80%
   :align: center

   The chat tab: ask a question and receive a streamed, source-backed
   answer.

Inspect
-------

The inspect tab is the transparency view.  It updates live as the graph
runs: each pipeline step appends an entry as it finishes -- for example
classification, retrieval, answering and evaluation -- with the step's
heading, how long it took, a short summary, and a collapsible **View
details** panel holding the raw structured data for that step.  That data
renders as syntax-highlighted JSON (multi-line string values such as
prompts are expanded for readability) with a copy button.  Entries are
grouped under a collapsible section per query (timestamp plus the query
text), and the pane keeps every query's section for the browser session
instead of clearing after each one.  This is what makes a Klea answer
inspectable: you can see which sources were retrieved and how the
pipeline arrived at its response.

.. figure:: /_static/images/20260916-klea-rag-inspection.png
   :alt: Klea RAG web interface inspect tab showing the per-step pipeline trace
   :width: 80%
   :align: center

   The inspect tab: the per-step trace behind the latest answer.

Status pane
-----------

The right drawer summarises the current chat: its name, the model used
for each role (with a settings button to change them), running token
totals, and the per-step state sections that stream while the graph runs.
Tool calls appear here as they run, marked ``running`` and replaced by
``ok``/``error`` when the round ends.  In the agent, the operating mode and
tool access level selectors appear here as well.

The settings button opens the model dialog.  Each role's model is chosen
with three fields -- a **provider** dropdown, a **model** dropdown, and an
optional **custom URL** -- filled from the `models.dev <https://models.dev>`_
catalogue.  Free text is still accepted for models that are not in the
catalogue (for example ``ollama:`` models or a self-hosted endpoint), and a
custom URL overrides the provider's default endpoint.  The dialog edits the
current chat's models when a chat is open and the per-session defaults
otherwise; saving in a chat also updates the session defaults ("last used"),
so new chats start from the same models.  **Manage API keys** opens the
provider-scoped credentials editor: keys are stored per provider on the
server, returned only in masked form, and expire after a period unused.

For a ``huggingface`` role the dialog also shows a **Run** choice
(hosted inference providers, or local) and, for the hosted backend, an
**inference provider** field (``auto``, ``cheapest``, ``fastest``,
``preferred``, or a specific provider).  A hosted model is therefore never
silently downloaded; ``Run: Local`` is the explicit opt-in.

.. note::

   The screenshots on this page are captured from a local deployment and
   may lag the latest interface.

.. seealso::

   * :doc:`../guides/create-and-use-rag` -- build a RAG system and
     query it through this interface
   * :doc:`rag` -- the pipeline the inspect tab exposes
