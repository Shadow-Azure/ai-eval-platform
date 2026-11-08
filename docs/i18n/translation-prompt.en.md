# Translation Prompt

English | [中文](translation-prompt.md)

To complete an English side, copy the template fence below and replace its three placeholders: terminology, Chinese source document, and Chinese filename. The output is the full English file; re-record the pair afterward.

```text
Translate the Chinese source document into English.
Use the supplied terminology exactly and preserve fenced content.
Keep headings, tables, lists, and link targets structurally mirrored.
Use this exact switcher line: English | [中文]({{source_basename}})

Terminology:
{{terminology}}

Source document:
{{source_document}}
```
