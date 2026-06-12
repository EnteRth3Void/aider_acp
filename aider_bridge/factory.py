from aider.coders import Coder
from aider.models import Model
from .io_bridge import ACPIO

def create_coder(io: ACPIO, model_name: str = "gpt-4o"):
    # Note: Aider will try to load the model. 
    # Ensure the environment variables for the provider are set.
    model = Model(model_name)
    
    # We use Coder.create to get the appropriate coder class based on the model/edit format
    coder = Coder.create(
        main_model=model,
        io=io,
        # Default settings for library usage
        auto_commits=False, 
        dirty_commits=False,
    )
    return coder
