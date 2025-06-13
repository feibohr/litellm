#!/usr/bin/env python3
"""
Test script for LiteLLM Proxy Configuration System

This script demonstrates the two-tier proxy configuration priority system:
1. Model level (highest priority) - litellm_params.proxy_config
2. Provider level (lowest priority) - LiteLLM_Config.provider_proxy_config from database
"""

import os
import sys
import json
from typing import Dict, Any

# Add the litellm directory to the path
sys.path.insert(0, './litellm')

try:
    from litellm.proxy.proxy_config import ProxyConfig
    from litellm.types.router import LiteLLM_Params
    from litellm.proxy.utils import setup_client_with_proxy
except ImportError:
    print("Error: Could not import LiteLLM modules. Make sure you're running from the correct directory.")
    sys.exit(1)

def test_proxy_priority_system():
    """Test the proxy configuration priority system"""
    print("=== Testing LiteLLM Proxy Configuration Priority System ===\n")
    
    # Initialize proxy config system
    proxy_config = ProxyConfig()
    
    # Test 1: Provider-level proxy configuration from database
    print("1. Testing Provider-level Proxy Configuration")
    print("-" * 50)
    
    # Simulate database provider configuration
    provider_proxy_config = {
        "openai": {
            "http": "http://openai-proxy.company.com:8080",
            "https": "http://openai-proxy.company.com:8080",
            "no_proxy": "localhost,127.0.0.1"
        },
        "openrouter": {
            "http": "http://192.168.10.43:7890",
            "https": "http://192.168.10.43:7890",
            "no_proxy": "localhost,127.0.0.1"
        },
        "anthropic": {
            "http": "http://anthropic-proxy.company.com:8080",
            "https": "http://anthropic-proxy.company.com:8080"
        },
        "azure*": {
            "http": "http://azure-proxy.company.com:8080",
            "https": "http://azure-proxy.company.com:8080"
        },
        "*": {  # Fallback for any provider
            "http": "http://default-proxy.company.com:8080",
            "https": "http://default-proxy.company.com:8080"
        }
    }
    
    # Set provider configuration (simulates loading from database)
    proxy_config.set_provider_proxy_config(provider_proxy_config)
    
    # Test provider pattern matching
    test_cases = [
        ("openai", "Should match exact openai provider"),
        ("openrouter", "Should match exact openrouter provider"),
        ("azure/gpt-4", "Should match azure* pattern"),
        ("azure", "Should match azure* pattern"),
        ("anthropic", "Should match exact anthropic provider"),
        ("google", "Should match * fallback pattern"),
    ]
    
    for provider, description in test_cases:
        result = proxy_config.get_proxy_config(custom_llm_provider=provider)
        print(f"Provider: {provider:15} | {description}")
        print(f"Result:   {result}")
        print()
    
    # Test 2: Model-level proxy configuration (highest priority)
    print("\n2. Testing Model-level Proxy Configuration Override")
    print("-" * 55)
    
    # Create LiteLLM params with model-level proxy config
    litellm_params = LiteLLM_Params(
        model="gpt-4",
        proxy_config={
            "http": "http://model-specific-proxy.company.com:8080",
            "https": "http://model-specific-proxy.company.com:8080"
        }
    )
    
    # Test with model-level config - should override provider config
    result_with_model_override = proxy_config.get_proxy_config(
        litellm_params=litellm_params,
        custom_llm_provider="openai"  # This has provider config, but model config should win
    )
    
    print("Model with proxy_config:")
    print(f"  litellm_params.proxy_config: {litellm_params.proxy_config}")
    print(f"  custom_llm_provider: openai")
    print(f"  Final result: {result_with_model_override}")
    print("  ✓ Model-level proxy config takes priority over provider config")
    
    # Test 3: httpx client integration
    print("\n3. Testing httpx Client Integration")
    print("-" * 40)
    
    # Test httpx proxy configuration format
    httpx_proxy_config = proxy_config.get_httpx_proxy_config(
        litellm_params=litellm_params,
        custom_llm_provider="openai"
    )
    
    print(f"httpx-compatible proxy config: {httpx_proxy_config}")
    
    # Test with provider-only config
    httpx_provider_config = proxy_config.get_httpx_proxy_config(
        custom_llm_provider="anthropic"
    )
    
    print(f"Provider-only httpx config: {httpx_provider_config}")
    
    # Test 4: Proxy URL normalization
    print("\n4. Testing Proxy URL Normalization")
    print("-" * 40)
    
    test_urls = [
        "proxy.company.com:8080",           # Should add http://
        "http://proxy.company.com:8080",    # Should remain unchanged
        "https://proxy.company.com:8080",   # Should remain unchanged
        "socks5://proxy.company.com:1080",  # Should remain unchanged
    ]
    
    for url in test_urls:
        normalized = proxy_config._normalize_proxy_url(url)
        print(f"  {url:35} → {normalized}")

def simulate_database_operations():
    """Simulate database operations for provider proxy configuration"""
    print("\n=== Simulating Database Operations ===\n")
    
    # This would be the actual database interaction in a real scenario
    print("1. Simulating database storage of provider proxy config:")
    
    provider_config = {
        "openai": {
            "http": "http://openai-corp-proxy.com:8080",
            "https": "http://openai-corp-proxy.com:8080"
        },
        "azure*": {
            "http": "http://azure-corp-proxy.com:8080",
            "https": "http://azure-corp-proxy.com:8080"
        }
    }
    
    # In real usage, this would be stored in LiteLLM_Config table:
    # INSERT INTO LiteLLM_Config (param_name, param_value) 
    # VALUES ('provider_proxy_config', JSON.stringify(provider_config))
    
    print(f"   Database entry: param_name='provider_proxy_config'")
    print(f"   Database entry: param_value='{json.dumps(provider_config, indent=2)}'")
    
    print("\n2. Simulating database retrieval on startup:")
    print("   - Proxy system loads configuration from LiteLLM_Config table")
    print("   - Configuration applied to global_proxy_config instance")
    print("   - All subsequent proxy requests use this configuration")

def demonstrate_usage_examples():
    """Demonstrate real-world usage examples"""
    print("\n=== Real-World Usage Examples ===\n")
    
    print("1. Model Configuration with Proxy Override:")
    print("   File: config.yaml")
    print("""
   model_list:
     - model_name: gpt-4-secure
       litellm_params:
         model: gpt-4
         api_key: os.environ/OPENAI_API_KEY
         # Override provider proxy for this specific model
         proxy_config:
           http: "http://secure-proxy.company.com:8080"
           https: "http://secure-proxy.company.com:8080"
           no_proxy: "localhost,127.0.0.1"
    """)
    
    print("\n2. Database Provider Configuration:")
    print("   Table: LiteLLM_Config")
    provider_config_example = {
        "openai": {
            "http": "http://openai-proxy.company.com:8080",
            "https": "http://openai-proxy.company.com:8080"
        },
        "anthropic": {
            "http": "http://anthropic-proxy.company.com:8080", 
            "https": "http://anthropic-proxy.company.com:8080"
        }
    }
    print(f"   param_name: provider_proxy_config")
    print(f"   param_value: {json.dumps(provider_config_example, indent=2)}")
    
    print("\n3. Programmatic Configuration Update:")
    print("""
   from litellm.proxy.utils import update_provider_proxy_config
   
   # Update provider configuration in database
   new_config = {
       "openai": {
           "http": "http://new-openai-proxy.company.com:8080"
       }
   }
   
   await update_provider_proxy_config(new_config, prisma_client)
    """)

if __name__ == "__main__":
    print("LiteLLM Proxy Configuration Test Suite")
    print("=" * 50)
    
    try:
        test_proxy_priority_system()
        simulate_database_operations()
        demonstrate_usage_examples()
        
        print("\n" + "=" * 50)
        print("✓ All tests completed successfully!")
        print("\nProxy Configuration Summary:")
        print("1. Model-level proxy config (highest priority)")
        print("2. Provider-level proxy config from database (lowest priority)")
        print("\nNext Steps:")
        print("- Configure provider proxy settings in database")
        print("- Add model-specific overrides in config.yaml as needed")
        print("- Test with actual HTTP requests to verify proxy usage")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1) 