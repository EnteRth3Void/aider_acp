import logging
from aider_bridge.factory import create_coder


class AiderAgent:
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.coder, self.io = create_coder()
        self.logger.info("AiderAgent initialized with coder and IO")
        print(id(self.io))

    def ask(self, prompt: str) -> str:
        self.logger.info("Asking prompt: %s", prompt)
        result = self.coder.run(prompt)

        if result:
            return str(result)

        return self.io.get_text()
