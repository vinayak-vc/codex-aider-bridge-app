# Graph Report - Test Repo

## Summary
- 4 nodes · 3 edges · 2 communities detected
- Extraction: 90% EXTRACTED · 10% INFERRED

## Community Hubs (Navigation)
- [[_COMMUNITY_Supervisor Workflow Core|Supervisor Workflow Core]]
- [[_COMMUNITY_Relay API Workflow|Relay API Workflow]]

## God Nodes (most connected - your core abstractions)
1. `SupervisorAgent` - 2 edges
2. `RepoScanner` - 2 edges

## Surprising Connections (you probably didn't know these)
- `Relay API` --uses--> `RepoScanner`  [INFERRED]

## Suggested Questions
- **Why does `RepoScanner` connect planning to relay?**
