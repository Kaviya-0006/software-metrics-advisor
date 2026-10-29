# Mixed-language version test

Use the included `sample_projects/demo_mixed_v1.zip`, then v2, then v3.

## Upload order
1. v1: choose **New project**.
2. v2: choose **New version of an existing project** and select the detected `demo_mixed` identity.
3. v3: again add it as the next version of `demo_mixed`.

## What this demonstrates
- Java + JavaScript in one project.
- V1 -> V2: intentional complexity/LOC growth and an added JavaScript module.
- V2 -> V3: refactoring and reduced repeated logic.
- Project trend should show a visible down move followed by an up move where the stored metrics change enough to cross the trend display thresholds.
- Language Breakdown shows Java and JavaScript separately, with file count, LOC, mean complexity, mean maintainability, and high/critical count.
- File-level comparison shows changed and added modules.
