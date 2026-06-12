from aider_bridge.factory import create_coder


class AiderAgent:
    def __init__(self):
        self.coder, self.io = create_coder()
        print(id(self.io))

    def ask(self, prompt: str) -> str:
        result = self.coder.run(prompt)

        if result:
            return str(result)

        return self.io.get_text()
