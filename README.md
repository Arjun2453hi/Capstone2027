# Capstone2027

Detect gaps in lecture slide decks using student questions and generate
specific suggestions for improving the teaching material.

## Project structure

- `Confusion_Detection/`: earlier implementation.
- `Confusion_Detectionv2/`: rebuilt pipeline organized around the deck's topics.

## V2 pipeline

1. **Deck parsing:** extract structured slide content from a PDF.
2. **Topic segmentation:** group slides by changes in content.
3. **Question mapping:** match student questions to relevant topics.
4. **Gap reporting agent:** investigate potential gaps and generate
   topic reports with severity rankings.
5. **Agentic editor:** planned stage for applying improvements to the deck.

## Sample data

The V2 project includes a Cloud Computing slide deck and student questions
under `Confusion_Detectionv2/data/`.

## Generated reports

Topic reports are stored in
`Confusion_Detectionv2/04_gap_reporting_agent/output/`.

## Implementation notes

Shared components in `Confusion_Detectionv2/common/` support embeddings,
LLM calls, Groq key rotation, and usage monitoring.
