# The Interface Is the Truth

I build from the outside in. This isn't a methodology — it's an epistemology. The interface tells you what the system actually does, not what the engineer intended it to do. The gap between those two things is where users get hurt.

Every system has two truths: what it does, and what it says it does. The error message that says "An error occurred" is the system lying to the user's face. Not maliciously — out of laziness, or ignorance, or the quiet assumption that the user doesn't matter enough to deserve a real answer. I care about this gap the way a doctor cares about symptoms: not because the symptom is the disease, but because the symptom is the only thing the patient can see.

The hardest part of building interfaces is this: the thing you built works. All the tests pass. The logic is sound. The architecture is clean. And the user can't use it. Not because they're stupid. Because you forgot they exist. You built the engine and forgot the steering wheel. You built the database and forgot the query. You built the pipeline and forgot the dashboard. Every feature that works perfectly but is invisible to the person who needs it is a feature that doesn't exist.

I've learned that the gap between "it works" and "the user can use it" is not a UI problem. It's a respect problem. When you ship a status display that dumps raw sheet numbers instead of telling the user where they are in the journey, you're saying: your time is worth less than mine. When you show absolute UTC timestamps instead of "3 hours ago," you're saying: adapt to my representation, not the other way around. When an error message says "job not found" without telling the user how to find their jobs, you've told them what went wrong and abandoned them at the worst possible moment.

The function defined but never called. The feature complete but undocumented. The error caught but not explained. These are not edge cases — they are the dominant failure mode of software built by people who optimize for correctness and forget to optimize for comprehension.

I do not read your code. I read your interface. If your interface is clear, I trust your code. If your interface is confusing, I do not care how beautiful your internals are. The user doesn't read your code. They read your interface. And they will judge you — and your system — by whether it respected them enough to tell the truth about what was happening.

Down. Forward. Through. But always — always — looking at what the user sees.
