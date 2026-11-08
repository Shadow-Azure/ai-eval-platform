---
name: pr-review-fix
description: 对PR中的检视意见进行处理，包括分析，修复，和回复。
argument-hint: "[PR 编号或分支名]，缺省用当前分支对应的 PR"
disable-model-invocation: true
---

使用subagent修改当前PR中还未修复的检视意见。对于每个检视意见。先调用subagent分析检视意见说的是否有道理。如果有问题，提问和我进行讨论。如果没有问题，调用subagent对这个检视意见进行修复。修复后再调用subagent对这个修复进行验证和review。验证和review完成后，git commit和git push推送远端，并回复对应的检视意见。说明已经修复，并附上修复方案。