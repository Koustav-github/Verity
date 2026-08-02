the record is being maintained after the reboot

1. verity sdk is working, serializes models and push it to the ingestion pipeline on server. The ingestion pipeline hands over the model to orchestrator to build artifacts. Nothing automated
2. Serializes and uploads model at staging and gets artifacts and stores model withOUT any extension. straight what is returned from the llm returns. and the orchestration is set up till this