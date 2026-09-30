UI — NiceGUI
============

The NiceGUI web frontend is composed *per app* (``klea_agent.ui.web`` /
``klea_rag.ui.web``, ADR-0031): each app builds its page from the shared
components below.  ``klea_utils`` provides only the reusable helpers and
components, never a full page.

Shared frontend helpers
-----------------------

.. automodule:: klea_utils.ui.web.nicegui.state
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.client
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.parser
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.entry
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.widgets
   :members:
   :show-inheritance:

Shared components
-----------------

.. automodule:: klea_utils.ui.web.nicegui.components.context
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.bootstrap
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.storage
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.theme
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.stream
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.chat_bubble
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.chat_area
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.chat_list
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.header
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.inspector
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.status_pane
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.model_dialog
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.input_area
   :members:
   :show-inheritance:

.. automodule:: klea_utils.ui.web.nicegui.components.initial_load
   :members:
   :show-inheritance:

Text helpers
------------

.. automodule:: klea_utils.ui.linkify
   :members:
   :show-inheritance:
