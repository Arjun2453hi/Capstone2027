# CLAUDE.md — 03_question_mapping

Scores every real student question against every topic produced by
`02_topic_segmentation` — topic → question direction, not question →
question clustering — keeping matches above a threshold along with
their score, so a question can legitimately match zero, one, or several
topics instead of being forced into agreement with other noisy,
independently-phrased questions. Not built yet — depends on
`02_topic_segmentation` having real, validated output to design against
first, per the project root CLAUDE.md's build order.
