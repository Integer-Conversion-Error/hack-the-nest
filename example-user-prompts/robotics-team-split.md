# Splitting a robotics club into two teams

I coach a school robotics club with six students: Ava, Ben, Cara, Dev, Eli and Fay. For next month's competition I have to split them into two teams of three.

Some pairs of students do not work well together. I gave each difficult pair a friction score from 1 to 10, where a higher score means more arguments when they are on the same team:

| Pair | Friction score |
| --- | --- |
| Ava and Ben | 8 |
| Ava and Cara | 3 |
| Ava and Fay | 1 |
| Ben and Dev | 6 |
| Ben and Eli | 4 |
| Cara and Dev | 7 |
| Cara and Eli | 2 |
| Dev and Eli | 3 |
| Dev and Fay | 5 |
| Eli and Fay | 9 |

Any pair not in the table gets along fine and has a friction score of 0.

A pair's friction only counts when both students are on the same team. I want the split that gives the lowest total friction, adding up both teams.

Please tell me who should be on each team and what the total friction is.

Eli and Fay have been getting along better lately, so I would also like to be able to rerun this with their score lowered from 9 to 2 to see whether the teams change.
