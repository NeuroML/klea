Web interface
=============

Klea's web interface is a `NiceGUI <https://nicegui.io/>`_ frontend that
talks to the backend API.  It is started with ``klea-rag web`` (RAG) or
``klea web`` (agent), and is the interface used by the hosted prototypes
linked from the :doc:`home page <../index>`.

The layout has three parts: a left drawer listing chat sessions, a centre
panel with two tabs (**chat** and **inspect**), and a right drawer with
per-chat state (the *status pane*).

Chat
----

The chat tab is where questions are asked.  Answers stream in as they are
generated, and each answer is grounded in the retrieved sources rather
than the model's memory alone.  The input box sits at the bottom; use the
left drawer to start a new chat or switch between existing ones, and the
status pane to choose the model for the chat.

.. figure:: /_static/images/20260916-klea-rag-chat.png
   :alt: Klea RAG web interface chat tab showing a question and a cited answer
   :width: 80%
   :align: center

   The chat tab: ask a question and receive a streamed, source-backed
   answer.

Inspect
-------

The inspect tab is the transparency view.  After a query completes it
lists one entry per pipeline step -- for example classification,
retrieval, answering and evaluation -- with the step's heading, how long
it took, a short summary, and a collapsible **View details** panel holding
the raw structured data for that step.  This is what makes a Klea answer
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
In the agent, the operating mode and tool access level selectors appear
here as well.

.. note::

   The screenshots on this page are captured from a local deployment and
   may lag the latest interface.

.. seealso::

   * :doc:`../guides/create-and-use-rag` -- build a RAG system and
     query it through this interface
   * :doc:`rag` -- the pipeline the inspect tab exposes
