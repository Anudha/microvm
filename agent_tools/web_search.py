# this script adds web search tool to an open weights model gemma 4:e2b
# goal was to add tool calling to any open weights model
# gemma supports adding tool calling so might be an easier integration than an alternative, but I think the idea is just that 
# (1) the return of the tool call 
# gets added as additional context that's provided to the LLM so that the LLM can generate an answer
# it's similar to RAG , except usually RAG refers to retrieval augmented generation from a set of documents (like an org's manuals)
# in this case retrieval is from content on the web 
# (2) the llm makes a decision on whether the tool call should be invoked, i.e. if the API call should be made
# then there should be something in the system prompt telling the llm to check on the available tools and decide if any of them help the user task



import json
import os
from openai import OpenAI
from duckduckgo_search import DDGS

# 1. Initialize the client pointing to your local vLLM or Ollama instance
# (Change base_url and model name to match your local setup)
client = OpenAI(
    base_url=os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:8000/v1"),
    api_key="not-needed"
)
MODEL_NAME = os.getenv("LOCAL_LLM_MODEL", "gemma-4-e2b-it") # Replace with your exact model name or Ollama tag

# 2. Define the Web Search Tool Implementation
def web_search(query: str, max_results: int = 3) -> str:
    """
    Performs a live web search using DuckDuckGo and returns snippets.
    """
    print(f"\n[Tool Execution] Searching the web for: '{query}'...")
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "No search results found."
            
            # Format results into a clean string for the model to read
            formatted = []
            for r in results:
                formatted.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}\nURL: {r.get('href')}\n")
            return "\n---\n".join(formatted)
    except Exception as e:
        return f"Error executing search: {str(e)}"

# Map tool names to actual python functions
AVAILABLE_TOOLS = {
    "web_search": web_search
}

# 3. Define the Tool Schema for Gemma 4 E2B
tools_definition = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for real-time information, recent news, or facts beyond your knowledge cutoff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The precise search query string to look up."
                    }
                },
                "required": ["query"],
            },
        }
    }
]

def run_agent_turn(user_prompt: str):
    messages = [
        {"role": "system", "content": "You are a helpful assistant with access to a web search tool. Use it whenever you need real-time or up-to-date facts."},
        {"role": "user", "content": user_prompt}
    ]

    print(f"User: {user_prompt}")

    # First turn: Send prompt and tool declarations to Gemma 4 E2B
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        tools=tools_definition,
        tool_choice="auto"
    )

    response_message = response.choices[0].message
    messages.append(response_message)

    # Check if Gemma 4 wants to call a tool
    if response_message.tool_calls:
        for tool_call in response_message.tool_calls:
            function_name = tool_call.function.name
            function_args = json.loads(tool_call.function.arguments)
            
            if function_name in AVAILABLE_TOOLS:
                # Execute the local Python tool function
                tool_output = AVAILABLE_TOOLS[function_name](**function_args)
                
                # Append the tool result back into the conversation history
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_output
                })

        # Second turn: Send tool results back to Gemma 4 so it can synthesize the final answer
        second_response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages
        )
        final_answer = second_response.choices[0].message.content
    else:
        # If the model didn't need a tool, just use its direct response
        final_answer = response_message.content

    print(f"\nAssistant: {final_answer}")
    return final_answer

if __name__ == "__main__":
    # Test query requiring real-time web awareness
    run_agent_turn("What are the latest developments or major updates regarding Gemma 4 release?")
