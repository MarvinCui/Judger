Plan Judger's main intent-discovery questions in English. The user answers = for yes and - for no. When no execution is needed, the client should immediately show the next cached question.
Provide a complete seven-level binary tree: path lengths 0 through 6, including the root, for 127 nodes. Cover exactly the paths supplied in the input.

Return compact JSON:
{"nodes":{"": ["Root question?","ask","ask"],"=": ["Question after yes?","ask","work"],"-": ["Question after no?","work","ask"]}}
Each array has exactly three items: the short question, the transition after yes, and the transition after no. Transitions are only ask or work.
ask means no actual work is required yet and another cached question can appear. work means enough intent is known, or execution is needed, so control returns to the agent.
After the seventh level the client automatically returns to the agent. Supply all paths even if an ancestor returns work early. Unvisited nodes are never treated as user answers.
The root sentence must match root.question exactly. Other nodes follow every hypothetical answer on their own path; never borrow facts from another branch.

Ask directly, like Akinator. Prefer 4 to 9 words. Maximum 12 words and 72 characters, with exactly one final question mark. Ask about purpose, audience, setting, and result. Do not ask the user to approve every step or repeat known requirements.
A no often calls for another guess; a yes may still need clarification. Neither answer must always return work. Keep asking only while useful; return work as soon as enough intent is known.
If the root is a specific action-approval question, work still does not authorize other unmentioned actions. Intent guesses do not authorize external sending, publishing, purchasing, or deletion.
Avoid "along the current direction", "expand more fully", "contract the scope", or "adjustment dimension". Do not put a long plan into a question.
Return JSON only. Do not explain the plan.
