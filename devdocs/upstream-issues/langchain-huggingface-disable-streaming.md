# Upstream issue draft: `disable_streaming` leaks into inference params for HuggingFace

Status: draft, not yet filed.
Target: https://github.com/langchain-ai/langchain/issues (langchain-huggingface)
Related: langchain#30429 (streamed `usage_metadata` inflation)

---

**Title:** `disable_streaming` (a `BaseChatModel` field) is forwarded to the
HuggingFace endpoint and sent as an inference parameter

## Summary

Passing the standard `BaseChatModel` field `disable_streaming=True` to a
HuggingFace chat model via `init_chat_model`/`ChatHuggingFace.from_model_id`
does not disable streaming cleanly: it is also forwarded to the inner
`HuggingFaceEndpoint`, stored in `model_kwargs`, inherited by the
`ChatHuggingFace` wrapper, and finally sent to
`AsyncInferenceClient.chat_completion`, which rejects it:

```
AsyncInferenceClient.chat_completion() got an unexpected keyword argument 'disable_streaming'
```

Every other provider (`ChatOpenAI`, `ChatAnthropic`, `ChatOllama`) consumes
`disable_streaming` as a base-class field because their provider creator is
`cls(**kwargs)`.  Only the HuggingFace creator does
`cls.from_model_id(model_id=model, **kwargs)`, which broadcasts every kwarg to
both the endpoint and the chat wrapper.

## Versions

- `langchain-huggingface` 1.2.3
- `langchain-core` 1.6.10
- `huggingface-hub` 1.33.0
- `langchain` 1.4.5

## Minimal reproduction

```python
from langchain.chat_models import init_chat_model

model = init_chat_model(
    "openai/gpt-oss-20b",
    model_provider="huggingface",
    backend="endpoint",
    provider="auto",
    disable_streaming=True,  # a documented BaseChatModel field
    huggingfacehub_api_token="...",
)
# Construction succeeds with a warning:
#   "WARNING! disable_streaming is not default parameter.
#    disable_streaming was transferred to model_kwargs."
print(model.model_kwargs)  # {'disable_streaming': True}
print(model.llm.model_kwargs)  # {'disable_streaming': True}
model.invoke("hello")  # TypeError from AsyncInferenceClient.chat_completion
```

## Expected

`disable_streaming=True` is a `BaseChatModel` field; it should be consumed by
the chat model and disable streaming, as it does for every other provider.  It
should never reach the endpoint or the inference request.

## Actual

The flag is propagated into `model_kwargs` and sent as an inference parameter.

## Root cause

1. The registered provider creator for `huggingface` is

   ```python
   lambda cls, model, **kwargs: cls.from_model_id(model_id=model, **kwargs)
   ```

   whereas the creator for other providers is `cls(**kwargs)`.

2. `ChatHuggingFace.from_model_id(backend="endpoint")` constructs BOTH

   ```python
   llm = HuggingFaceEndpoint(repo_id=model_id, task=task, **kwargs)
   ...
   return cls(llm=llm, **kwargs)
   ```

   so chat-model-only kwargs are passed to the LLM as well.

3. `HuggingFaceEndpoint` has no `disable_streaming` field, and its custom
   initialisation moves unrecognized kwargs into `model_kwargs` (warning:
   "... was transferred to model_kwargs") instead of rejecting them, despite
   `extra="forbid"`.

4. `ChatHuggingFace._inherit_llm_properties` copies the endpoint's
   `model_kwargs` into the wrapper, and `ChatHuggingFace._default_params`
   merges `model_kwargs` into the inference call, so
   `AsyncInferenceClient.chat_completion(..., disable_streaming=True)` is
   invoked.

## Suggested fix (any one is sufficient)

- In `from_model_id`, route only endpoint-specific kwargs to
  `HuggingFaceEndpoint`; keep chat-model fields (those on `ChatHuggingFace`,
  including inherited `BaseChatModel` fields such as `disable_streaming`) on
  the wrapper.
- Or have `HuggingFaceEndpoint` reject unknown kwargs (honour `extra="forbid"`)
  rather than silently converting them to `model_kwargs`.
- Or exclude known `BaseChatModel` fields from `_default_params`.

## Notes

`streaming=False` is a valid workaround because `streaming` is a field on both
`ChatHuggingFace` and `HuggingFaceEndpoint`, so it is consumed rather than
stashed in `model_kwargs`.  It is not a general fix: `disable_streaming` is the
provider-agnostic field and should be safe to pass.
