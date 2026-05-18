try:
    from langfuse.openai import openai
except ImportError:
    print("Warning: openai not found. Please install via 'pip install openai'")
    openai = None
