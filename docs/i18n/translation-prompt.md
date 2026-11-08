# 翻译提示词

[English](translation-prompt.en.md) | 中文

补英文侧时复制下面的模板围栏，并替换三个占位符：术语表、中文源文档和中文文件名。输出是英文侧完整内容；随后重录配对。

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
