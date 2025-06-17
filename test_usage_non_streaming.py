import os
import json
import litellm
from litellm.proxy.proxy_config import global_proxy_config

# Test non-streaming usage information
def test_openrouter_non_streaming_usage():
    """Test that usage information is returned for non-streaming OpenRouter requests"""
    
    # Configure multi-proxy settings
    os.environ['LITELLM_MULTI_PROXY_CONFIG'] = json.dumps({
        "openrouter": [
            {"http": "http://192.168.10.33:7890", "https": "http://192.168.10.33:7890"},
            {"http": "http://192.168.10.43:7890", "https": "http://192.168.10.43:7890"}
        ]
    })
    
    print("🧪 Testing non-streaming OpenRouter usage information...")
    
    try:
        # Make a non-streaming request
        response = litellm.completion(
            model="openrouter/meta-llama/llama-3.1-8b-instruct:free",
            messages=[{"role": "user", "content": "Say hello"}],
            api_key=os.getenv("OPENROUTER_API_KEY"),
            stream=False,  # Non-streaming
            max_tokens=10
        )
        
        print(f"✅ Response received: {response}")
        print(f"📊 Usage information: {response.usage}")
        
        if response.usage:
            print(f"  - Prompt tokens: {response.usage.prompt_tokens}")
            print(f"  - Completion tokens: {response.usage.completion_tokens}")
            print(f"  - Total tokens: {response.usage.total_tokens}")
            print("✅ SUCCESS: Usage information is present!")
        else:
            print("❌ FAILED: No usage information returned!")
            return False
        
        return True
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_openrouter_non_streaming_usage() 