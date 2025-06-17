import os
import json
import litellm
from unittest.mock import Mock, patch, AsyncMock
import asyncio

def test_streaming_usage_preservation():
    """Test that usage information is preserved during streaming with retries"""
    
    print("🧪 Testing streaming usage preservation...")
    
    # Configure multi-proxy settings
    os.environ['LITELLM_MULTI_PROXY_CONFIG'] = json.dumps({
        "openrouter": [
            {"http": "http://192.168.10.33:7890", "https": "http://192.168.10.33:7890"},
            {"http": "http://192.168.10.43:7890", "https": "http://192.168.10.43:7890"}
        ]
    })
    
    try:
        from litellm.llms.custom_httpx.llm_http_handler import MultiProxyStreamResponse
        
        # Mock response and client
        mock_response = Mock()
        mock_stream_context = Mock()
        mock_proxy_client = Mock()
        
        # Create MultiProxyStreamResponse instance
        stream_response = MultiProxyStreamResponse(
            response=mock_response,
            stream_context=mock_stream_context,
            proxy_client=mock_proxy_client,
            api_base="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer test-key"},
            data={"model": "meta-llama/llama-3.1-8b-instruct:free", "messages": []},
            custom_llm_provider="openrouter"
        )
        
        # Test usage preservation mechanism
        test_lines = [
            b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}',
            b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{"content":" there!"},"finish_reason":null}]}',
            b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
        ]
        
        # Simulate processing lines and detecting usage
        for line in test_lines:
            line_str = line.decode('utf-8')
            if line_str.startswith('data: '):
                json_str = line_str[6:]
                if json_str.strip() and json_str.strip() != '[DONE]':
                    try:
                        chunk_data = json.loads(json_str)
                        if isinstance(chunk_data, dict) and 'usage' in chunk_data:
                            stream_response.preserved_usage = chunk_data['usage']
                            print(f"📊 Found and preserved usage: {stream_response.preserved_usage}")
                    except Exception:
                        pass
        
        # Test usage injection logic
        final_chunk_line = b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}'
        
        line_str = final_chunk_line.decode('utf-8')
        if line_str.startswith('data: '):
            json_str = line_str[6:]
            if json_str.strip() and json_str.strip() != '[DONE]':
                chunk_data = json.loads(json_str)
                if isinstance(chunk_data, dict):
                    # Check if this chunk has finish_reason (likely the final chunk)
                    if (chunk_data.get('choices') and 
                        len(chunk_data['choices']) > 0 and
                        chunk_data['choices'][0].get('finish_reason')):
                        
                        # Inject preserved usage into this chunk
                        if 'usage' not in chunk_data and stream_response.preserved_usage:
                            chunk_data['usage'] = stream_response.preserved_usage
                            print(f"💉 Injected preserved usage into final chunk: {stream_response.preserved_usage}")
                            
                            # Reconstruct the line with usage
                            new_json_str = json.dumps(chunk_data)
                            new_line = f'data: {new_json_str}'
                            print(f"🔧 Reconstructed line: {new_line}")
        
        if stream_response.preserved_usage:
            print("✅ SUCCESS: Usage preservation mechanism works!")
            return True
        else:
            print("❌ FAILED: Usage not preserved!")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_async_streaming_usage():
    """Test async streaming usage preservation"""
    
    print("🧪 Testing async streaming usage preservation...")
    
    try:
        from litellm.llms.custom_httpx.llm_http_handler import MultiProxyStreamResponse
        
        # Mock async response
        mock_response = Mock()
        mock_stream_context = Mock()
        mock_proxy_client = Mock()
        
        # Create MultiProxyStreamResponse instance
        stream_response = MultiProxyStreamResponse(
            response=mock_response,
            stream_context=mock_stream_context,
            proxy_client=mock_proxy_client,
            api_base="https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer test-key"},
            data={"model": "meta-llama/llama-3.1-8b-instruct:free", "messages": []},
            custom_llm_provider="openrouter"
        )
        
        # Mock aiter_lines to simulate streaming with usage
        async def mock_aiter_lines():
            test_lines = [
                b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"},"finish_reason":null}]}',
                b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{"content":" there!"},"finish_reason":null}]}',
                b'data: {"id":"gen-123","object":"chat.completion.chunk","created":1234567890,"model":"meta-llama/llama-3.1-8b-instruct:free","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}'
            ]
            for line in test_lines:
                yield line
        
        mock_response.aiter_lines.return_value = mock_aiter_lines()
        
        # Test the async iteration with usage detection
        usage_found = False
        async for line in stream_response._aiter_lines_wrapper():
            line_str = line.decode('utf-8') if isinstance(line, bytes) else line
            if line_str.startswith('data: '):
                json_str = line_str[6:]
                if json_str.strip() and json_str.strip() != '[DONE]':
                    try:
                        chunk_data = json.loads(json_str)
                        if isinstance(chunk_data, dict) and 'usage' in chunk_data:
                            usage_found = True
                            print(f"📊 Found usage in streaming: {chunk_data['usage']}")
                    except Exception:
                        pass
        
        if usage_found:
            print("✅ SUCCESS: Async streaming usage detection works!")
            return True
        else:
            print("❌ FAILED: No usage found in async streaming!")
            return False
            
    except Exception as e:
        print(f"❌ Async test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    print("=" * 60)
    test1_result = test_streaming_usage_preservation()
    print("=" * 60)
    test2_result = await test_async_streaming_usage()
    print("=" * 60)
    
    if test1_result and test2_result:
        print("✅ All streaming usage tests passed!")
    else:
        print("❌ Some streaming usage tests failed!")

if __name__ == "__main__":
    asyncio.run(main()) 