from verity import Evaluator

print("Initializing Evaluator...")
evaluator = Evaluator(verbose=True)

prompt = "What is machine learning?"
response = "Machine learning is a subset of AI where systems learn from data."
context = "ML is a branch of AI that learns from data without explicit programming."

print("\n" + "="*50)
print("Evaluating LLM output...")
print("="*50)

result = evaluator.evaluate(
    prompt=prompt,
    response=response,
    context=context,
    detectors=["embedding"]
)

print("\n" + "="*50)
print("RESULTS")
print("="*50)
print(result)
print(f"Final Score: {result.final_score:.2f}")
print(f"Alerts: {result.alerts}")
print(f"Recommendations: {result.recommendations}")