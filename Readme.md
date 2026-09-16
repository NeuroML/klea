# Klea

Knowledge vaLidated Expert AI Assistant for scientific research.

Grounded, citation-backed answers over your own research sources.

[![GitHub CI](https://github.com/NeuroML/klea/actions/workflows/ci.yml/badge.svg)](https://github.com/NeuroML/klea/actions/workflows/ci.yml)
[![GitHub](https://img.shields.io/github/license/NeuroML/klea)](https://github.com/NeuroML/klea/blob/main/LICENSE)
[![GitHub pull requests](https://img.shields.io/github/issues-pr/NeuroML/klea)](https://github.com/NeuroML/klea/pulls)
[![GitHub issues](https://img.shields.io/github/issues/NeuroML/klea)](https://github.com/NeuroML/klea/issues)
[![GitHub Org's stars](https://img.shields.io/github/stars/NeuroML?style=social)](https://github.com/NeuroML)
[![Twitter Follow](https://img.shields.io/twitter/follow/NeuroML?style=social)](https://twitter.com/NeuroML)
[![Gitter](https://badges.gitter.im/NeuroML/community.svg)](https://gitter.im/NeuroML/community?utm_source=badge&utm_medium=badge&utm_campaign=pr-badge)
<!-- ALL-CONTRIBUTORS-BADGE:START - Do not remove or modify this section -->
[![All Contributors](https://img.shields.io/badge/all_contributors-5-orange.svg?style=flat-square)](#contributors-)
<!-- ALL-CONTRIBUTORS-BADGE:END -->


This repository contains multiple packages that together make up the AI assistant, Klea.

## Why Klea

Klea is a research assistant that grounds its answers in your own sources.
Queries are answered from domain-configurable knowledge stores (documents,
papers, databases) rather than the model's memory alone: answers are checked
by an evaluation loop, cite the documents they drew on, and record their
provenance so researchers can inspect and verify the output.  When a query
cannot be grounded in the available sources, Klea flags the fallback rather
than presenting it as confident fact.

On top of that grounded retrieval core sits a general-purpose research agent
for the research lifecycle -- literature review, hypothesis generation,
planning, coding, pipeline execution, and analysis.  Because capabilities
are supplied by MCP tools and domain-configurable knowledge stores rather
than hard-coded, the same assistant extends to new research tasks as tools
are added.  That focus on grounded, cited answers is what sets Klea apart
from general-purpose chat and coding assistants.

**Status:** the RAG pipeline (`klea_rag` / `klea_utils`) is ready to use
today.  The agent (`klea_agent`) is under active development, with an initial
release planned.

## Features

**Retrieval (RAG)**

- Multi-domain knowledge stores with automatic query classification and routing
- Grounded answers -- retrieval plus an evaluation loop, with every response
  recording the sources and tools it drew on
- Hybrid retrieval combining dense vector search and BM25 keyword search, fused
  with Reciprocal Rank Fusion and a recency tiebreaker
- Pluggable vector stores: Chroma, Qdrant, and PGVector
- Document ingestion via Docling with OCR, automatic bibliographic metadata
  extraction (DOI resolution through Crossref, OpenAlex, and Semantic Scholar),
  and domain-scoped metadata filters

**Agent (work in progress)**

- General-purpose research agent for literature review, hypothesis generation,
  planning, coding, pipeline execution, and analysis
- General and Scientific operating modes, with chat-versus-task routing and a
  planner -- Scientific mode awaits a curated knowledge source
- A human plan-review step, currently auto-approved; interactive pause/resume
  is pending
- Capabilities supplied by MCP tools rather than hard-coded, so the agent
  extends as tools are added
- Tool access levels (`read_only` / `full`) and sandboxed command execution
  with a wall-clock backstop

**Interfaces and models**

- CLI, FastAPI server, NiceGUI web UI, Streamlit, and TUI
- Bring-your-own LLM: OpenAI-compatible, Anthropic, HuggingFace, and custom
  endpoints, with runtime model switching and prompt caching
- NeuroML MCP tools: model validation, OSB and NeuroML-DB lookups, web search,
  and sandboxed code execution

The packages included, one in each folder are:

- agent_pkg: the main Klea agent, a general-purpose research agent (coding, workflows, analysis, hypothesis generation)
- rag_pkg: a generic RAG implementation, primarily consumed by the agent
- mcp_pkg: MCP server for NeuroML
- utils_pkg: common utility functions used by other packages

Please see the individual Readme files for more information.

## Funding

Klea is funded by the [BioFAIR](https://biofair.uk/) Pathfinder Projects
grant ["Creating AI-enabled analysis pipelines for FAIR neuroscience
data"](https://biofair.uk/updates/2026/biofair-pathfinder-projects-launch-with-800k-to-transform-uk-fair-practices/),
awarded to [Padraig Gleeson](https://profiles.ucl.ac.uk/11654-padraig-gleeson)
and [Ankur Sinha](https://profiles.ucl.ac.uk/77575-ankur-sinha) at
[University College London](https://openneuroai.org/).

As part of this Pathfinder project, Klea is being tested for neuroscience
research, through the NeuroML-specific `nml-mcp` server and the curated
NeuroML vector stores.

![BioFAIR logo](docs/_static/biofair-logo.png)

## Contributors ✨

Thanks goes to these wonderful people ([emoji key](https://allcontributors.org/docs/en/emoji-key)):

<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->
<!-- prettier-ignore-start -->
<!-- markdownlint-disable -->
<table>
  <tbody>
    <tr>
      <td align="center" valign="top" width="14.28%"><a href="https://ankursinha.in/"><img src="https://avatars.githubusercontent.com/u/102575?v=4?s=100" width="100px;" alt="Ankur Sinha"/><br /><sub><b>Ankur Sinha</b></sub></a><br /><a href="https://github.com/NeuroML/klea/commits?author=sanjayankur31" title="Code">💻</a> <a href="https://github.com/NeuroML/klea/commits?author=sanjayankur31" title="Documentation">📖</a> <a href="https://github.com/NeuroML/klea/issues?q=author%3Asanjayankur31" title="Bug reports">🐛</a> <a href="#blog-sanjayankur31" title="Blogposts">📝</a> <a href="#data-sanjayankur31" title="Data">🔣</a> <a href="#design-sanjayankur31" title="Design">🎨</a> <a href="#example-sanjayankur31" title="Examples">💡</a> <a href="#financial-sanjayankur31" title="Financial">💵</a> <a href="#ideas-sanjayankur31" title="Ideas, Planning, & Feedback">🤔</a> <a href="#infra-sanjayankur31" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="#maintenance-sanjayankur31" title="Maintenance">🚧</a> <a href="#promotion-sanjayankur31" title="Promotion">📣</a> <a href="#projectManagement-sanjayankur31" title="Project Management">📆</a> <a href="#research-sanjayankur31" title="Research">🔬</a> <a href="https://github.com/NeuroML/klea/pulls?q=is%3Apr+reviewed-by%3Asanjayankur31" title="Reviewed Pull Requests">👀</a> <a href="#security-sanjayankur31" title="Security">🛡️</a> <a href="https://github.com/NeuroML/klea/commits?author=sanjayankur31" title="Tests">⚠️</a> <a href="#tutorial-sanjayankur31" title="Tutorials">✅</a></td>
      <td align="center" valign="top" width="14.28%"><a href="http://www.opensourcebrain.org/"><img src="https://avatars.githubusercontent.com/u/1556687?v=4?s=100" width="100px;" alt="Padraig Gleeson"/><br /><sub><b>Padraig Gleeson</b></sub></a><br /><a href="https://github.com/NeuroML/klea/commits?author=pgleeson" title="Code">💻</a> <a href="https://github.com/NeuroML/klea/commits?author=pgleeson" title="Documentation">📖</a> <a href="https://github.com/NeuroML/klea/issues?q=author%3Apgleeson" title="Bug reports">🐛</a> <a href="#financial-pgleeson" title="Financial">💵</a> <a href="#ideas-pgleeson" title="Ideas, Planning, & Feedback">🤔</a> <a href="#infra-pgleeson" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a> <a href="#maintenance-pgleeson" title="Maintenance">🚧</a> <a href="#promotion-pgleeson" title="Promotion">📣</a> <a href="#projectManagement-pgleeson" title="Project Management">📆</a> <a href="#research-pgleeson" title="Research">🔬</a> <a href="https://github.com/NeuroML/klea/pulls?q=is%3Apr+reviewed-by%3Apgleeson" title="Reviewed Pull Requests">👀</a> <a href="#security-pgleeson" title="Security">🛡️</a> <a href="https://github.com/NeuroML/klea/commits?author=pgleeson" title="Tests">⚠️</a> <a href="#tutorial-pgleeson" title="Tutorials">✅</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://khanak-khandelwal-portfolio.vercel.app"><img src="https://avatars.githubusercontent.com/u/186187285?v=4?s=100" width="100px;" alt="Khanak Khandelwal"/><br /><sub><b>Khanak Khandelwal</b></sub></a><br /><a href="https://github.com/NeuroML/klea/commits?author=khanak0509" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/Coderdhanush2003"><img src="https://avatars.githubusercontent.com/u/105554879?v=4?s=100" width="100px;" alt="Dhanush Shankar"/><br /><sub><b>Dhanush Shankar</b></sub></a><br /><a href="https://github.com/NeuroML/klea/commits?author=Coderdhanush2003" title="Code">💻</a></td>
      <td align="center" valign="top" width="14.28%"><a href="https://github.com/dhanushshankar-hq"><img src="https://avatars.githubusercontent.com/u/105554879?v=4?s=100" width="100px;" alt="Dhanush Shankar"/><br /><sub><b>Dhanush Shankar</b></sub></a><br /><a href="https://github.com/NeuroML/klea/commits?author=dhanushshankar-hq" title="Code">💻</a></td>
    </tr>
  </tbody>
  <tfoot>
    <tr>
      <td align="center" size="13px" colspan="7">
        <img src="https://raw.githubusercontent.com/all-contributors/all-contributors-cli/1b8533af435da9854653492b1327a23a4dbd0a10/assets/logo-small.svg">
          <a href="https://all-contributors.js.org/docs/en/bot/usage">Add your contributions</a>
        </img>
      </td>
    </tr>
  </tfoot>
</table>

<!-- markdownlint-restore -->
<!-- prettier-ignore-end -->

<!-- ALL-CONTRIBUTORS-LIST:END -->

This project follows the [all-contributors](https://github.com/all-contributors/all-contributors) specification. Contributions of any kind welcome!
