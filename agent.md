The Agentic part of this project will be called "Assembler". Verity-Assembler

The step-by-step working of the agentic system:
1. Identify the type of model created by the developer
2. Start the experiments server and create the evals run on the model hosted on verity's server.
3. based on evals score, it will decide whether to revert back or to continue to the api-fication and hosting
5. Model registers considering it passed the evals part.
4. Considering evals test passed, now models will be live and running on the server with suitable metrics live and systemic metrics sticking along with it.
5. Thats  where the deployment side ends but the model still sticks to each model and flagging an issue as soon as accuracy or quality of response drops below a certain threshold and informs the developer.

decision making required:
1. is the model standard xgboost/sklearn model? (Y/N)
2. type of the model and suitable metrics that can be observed against the model.
3. does the model scores good(good can be defined as when model exceeds certain value at threshold). [Recommend how can i apply decisions]