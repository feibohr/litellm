import os
import json
import litellm
from unittest.mock import Mock, patch
import httpx

def test_usage_transformation():
    """Test how usage information is handled in transform_response"""
    
    print("🧪 Testing usage transformation logic...")
    
    # Create a mock response that mimics OpenRouter's response format
    mock_response_data = {
        "id": "gen-1234567890",
        "object": "chat.completion",
        "created": 1234567890,
        "model": "meta-llama/llama-3.1-8b-instruct:free",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you today?"
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18
        }
    }
    
    # Create mock httpx response
    mock_response = Mock(spec=httpx.Response)
    mock_response.json.return_value = mock_response_data
    mock_response.text = json.dumps(mock_response_data)
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    
    # Test the conversion function
    try:
        from litellm.litellm_core_utils.llm_response_utils.convert_dict_to_response import convert_to_model_response_object
        from litellm.utils import ModelResponse
        
        # Create a model response object
        model_response = ModelResponse()
        
        # Test the conversion
        result = convert_to_model_response_object(
            response_object=mock_response_data,
            model_response_object=model_response,
            response_type="completion"
        )
        
        print(f"✅ Conversion successful!")
        print(f"📊 Result usage: {result.usage}")
        
        if result.usage:
            print(f"  - Prompt tokens: {result.usage.prompt_tokens}")
            print(f"  - Completion tokens: {result.usage.completion_tokens}")
            print(f"  - Total tokens: {result.usage.total_tokens}")
            print("✅ SUCCESS: Usage conversion works correctly!")
        else:
            print("❌ FAILED: Usage information not converted!")
            
        return result.usage is not None
        
    except Exception as e:
        print(f"❌ Conversion failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_openrouter_config_usage():
    """Test OpenRouter configuration specifically"""
    
    print("🧪 Testing OpenRouter configuration...")
    
    try:
        from litellm.llms.openrouter.chat.transformation import OpenrouterConfig
        from litellm.utils import ModelResponse
        from litellm._logging import verbose_logger
        
        # Create config instance
        config = OpenrouterConfig()
        
        # Mock response data with usage
        mock_response_data = {
            "id": "gen-1234567890",
            "object": "chat.completion",
            "created": 1234567890,
            "model": "meta-llama/llama-3.1-8b-instruct:free",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help you today?"
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "total_tokens": 18
            }
        }
        
        # Create mock httpx response
        mock_response = Mock(spec=httpx.Response)
        mock_response.json.return_value = mock_response_data
        mock_response.text = json.dumps(mock_response_data)
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        
        # Create model response
        model_response = ModelResponse()
        
        # Mock logging object
        logging_obj = Mock()
        logging_obj.post_call = Mock()
        
        # Test transform_response
        result = config.transform_response(
            model="meta-llama/llama-3.1-8b-instruct:free",
            raw_response=mock_response,
            model_response=model_response,
            logging_obj=logging_obj,
            request_data={"messages": [{"role": "user", "content": "Hello"}]},
            messages=[{"role": "user", "content": "Hello"}],
            optional_params={},
            litellm_params={},
            encoding=None,
            api_key="test-key",
            json_mode=False
        )
        
        print(f"✅ Transform successful!")
        print(f"📊 Result usage: {result.usage}")
        
        if result.usage:
            print(f"  - Prompt tokens: {result.usage.prompt_tokens}")
            print(f"  - Completion tokens: {result.usage.completion_tokens}")
            print(f"  - Total tokens: {result.usage.total_tokens}")
            print("✅ SUCCESS: OpenRouter transform_response works correctly!")
        else:
            print("❌ FAILED: OpenRouter transform_response doesn't preserve usage!")
            
        return result.usage is not None
        
    except Exception as e:
        print(f"❌ OpenRouter config test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    test1_result = test_usage_transformation()
    print("=" * 60)
    test2_result = test_openrouter_config_usage()
    print("=" * 60)
    
    if test1_result and test2_result:
        print("✅ All tests passed! Usage should be working.")
    else:
        print("❌ Some tests failed. There may be an issue with usage handling.") 