import os
import json
import litellm

def test_simple_usage_preservation():
    """Simple test to check if usage is preserved in streaming"""
    
    print("🧪 Testing simple usage preservation...")
    
    try:
        from litellm.litellm_core_utils.streaming_handler import calculate_total_usage
        from litellm.types.utils import ModelResponseStream, StreamingChoices, Delta, Usage
        from litellm.utils import Usage as LiteLLMUsage
        
        # Create mock chunks with usage information
        chunks = []
        
        # First chunk - no usage
        chunk1 = ModelResponseStream(
            id="gen-123",
            object="chat.completion.chunk",
            created=1234567890,
            model="meta-llama/llama-3.1-8b-instruct:free",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content="Hello"),
                    finish_reason=None
                )
            ],
            usage=None
        )
        chunks.append(chunk1)
        
        # Second chunk - no usage
        chunk2 = ModelResponseStream(
            id="gen-123", 
            object="chat.completion.chunk",
            created=1234567890,
            model="meta-llama/llama-3.1-8b-instruct:free",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content=" there!"),
                    finish_reason=None
                )
            ],
            usage=None
        )
        chunks.append(chunk2)
        
        # Final chunk - with usage
        final_usage = LiteLLMUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15
        )
        
        chunk3 = ModelResponseStream(
            id="gen-123",
            object="chat.completion.chunk", 
            created=1234567890,
            model="meta-llama/llama-3.1-8b-instruct:free",
            choices=[
                StreamingChoices(
                    index=0,
                    delta=Delta(content=""),
                    finish_reason="stop"
                )
            ],
            usage=final_usage
        )
        chunks.append(chunk3)
        
        print(f"📊 Chunk 1 usage: {chunk1.usage}")
        print(f"📊 Chunk 2 usage: {chunk2.usage}")
        print(f"📊 Chunk 3 usage: {chunk3.usage}")
        
        # Test calculate_total_usage function
        total_usage = calculate_total_usage(chunks)
        
        print(f"✅ Total calculated usage: {total_usage}")
        
        if total_usage and total_usage.prompt_tokens == 10 and total_usage.completion_tokens == 5:
            print("✅ SUCCESS: Usage calculation works correctly!")
            return True
        else:
            print("❌ FAILED: Usage calculation failed!")
            return False
            
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    result = test_simple_usage_preservation()
    if result:
        print("🎉 Simple usage test PASSED!")
    else:
        print("💥 Simple usage test FAILED!") 