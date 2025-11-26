import asyncio
import time
import json
import argparse
import aiohttp
import matplotlib.pyplot as plt
from datetime import datetime
from colorama import Fore, Style, init

# Initialize colorama
init()

async def make_request(session, url, endpoint, method="GET", data=None, headers=None):
    full_url = f"{url}{endpoint}"
    start_time = time.time()
    success = False
    status_code = None
    
    try:
        if method == "GET":
            async with session.get(full_url, headers=headers) as response:
                await response.text()
                status_code = response.status
                success = 200 <= status_code < 300
        elif method == "POST":
            async with session.post(full_url, json=data, headers=headers) as response:
                await response.text()
                status_code = response.status
                success = 200 <= status_code < 300
    except Exception as e:
        print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
        return False, time.time() - start_time, None
        
    response_time = time.time() - start_time
    return success, response_time, status_code

async def run_load_test(url, num_requests, concurrency, endpoints, auth_endpoint=None, auth_data=None):
    print(f"{Fore.CYAN}Starting load test against {url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Total requests: {num_requests}, Concurrency: {concurrency}{Style.RESET_ALL}")
    
    results = {endpoint["name"]: {"response_times": [], "success_count": 0, "failure_count": 0} 
               for endpoint in endpoints}
    
    # Set up authentication if needed
    auth_token = None
    if auth_endpoint and auth_data:
        async with aiohttp.ClientSession() as session:
            print(f"{Fore.YELLOW}Authenticating...{Style.RESET_ALL}")
            success, resp_time, status = await make_request(
                session, url, auth_endpoint["path"], 
                method=auth_endpoint["method"], 
                data=auth_data
            )
            if success:
                print(f"{Fore.GREEN}Authentication successful{Style.RESET_ALL}")
                # You might need to adjust this depending on your API's response format
                # auth_token = response_json.get("access_token")
            else:
                print(f"{Fore.RED}Authentication failed with status {status}{Style.RESET_ALL}")
                return
    
    headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else None
    
    semaphore = asyncio.Semaphore(concurrency)
    
    async def controlled_request(endpoint_info):
        async with semaphore:
            endpoint_name = endpoint_info["name"]
            endpoint_path = endpoint_info["path"]
            method = endpoint_info.get("method", "GET")
            data = endpoint_info.get("data")
            
            async with aiohttp.ClientSession() as session:
                success, response_time, status = await make_request(
                    session, url, endpoint_path, method=method, data=data, headers=headers
                )
                
                if success:
                    results[endpoint_name]["success_count"] += 1
                    results[endpoint_name]["response_times"].append(response_time)
                    print(f"{Fore.GREEN}✓ {endpoint_name} - {response_time:.4f}s{Style.RESET_ALL}")
                else:
                    results[endpoint_name]["failure_count"] += 1
                    print(f"{Fore.RED}✗ {endpoint_name} - Status: {status}{Style.RESET_ALL}")
    
    tasks = []
    for _ in range(num_requests):
        for endpoint in endpoints:
            tasks.append(controlled_request(endpoint))
    
    start_time = time.time()
    await asyncio.gather(*tasks)
    total_time = time.time() - start_time
    
    # Calculate and display results
    print(f"\n{Fore.CYAN}=== Load Test Results ==={Style.RESET_ALL}")
    print(f"Total time: {total_time:.2f} seconds")
    print(f"Total requests made: {num_requests * len(endpoints)}")
    print(f"Requests per second: {(num_requests * len(endpoints)) / total_time:.2f}")
    
    for endpoint_name, data in results.items():
        success_count = data["success_count"]
        failure_count = data["failure_count"]
        total = success_count + failure_count
        success_rate = (success_count / total) * 100 if total > 0 else 0
        
        response_times = data["response_times"]
        if response_times:
            avg_time = sum(response_times) / len(response_times)
            min_time = min(response_times)
            max_time = max(response_times)
            p95_time = sorted(response_times)[int(len(response_times) * 0.95)] if response_times else 0
            
            print(f"\n{Fore.YELLOW}Endpoint: {endpoint_name}{Style.RESET_ALL}")
            print(f"Success rate: {success_rate:.2f}% ({success_count}/{total})")
            print(f"Avg response time: {avg_time:.4f}s")
            print(f"Min response time: {min_time:.4f}s")
            print(f"Max response time: {max_time:.4f}s")
            print(f"95th percentile: {p95_time:.4f}s")
    
    # Generate and save graphs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for endpoint_name, data in results.items():
        if data["response_times"]:
            plt.figure(figsize=(10, 6))
            plt.plot(data["response_times"])
            plt.title(f"Response Times: {endpoint_name}")
            plt.xlabel("Request #")
            plt.ylabel("Response Time (s)")
            plt.grid(True)
            filename = f"load_test_{endpoint_name.replace(' ', '_')}_{timestamp}.png"
            plt.savefig(filename)
            print(f"{Fore.CYAN}Saved response time graph to {filename}{Style.RESET_ALL}")
    
    # Save raw results as JSON
    json_filename = f"load_test_results_{timestamp}.json"
    with open(json_filename, 'w') as f:
        json.dump({
            "summary": {
                "total_time": total_time,
                "total_requests": num_requests * len(endpoints),
                "requests_per_second": (num_requests * len(endpoints)) / total_time
            },
            "endpoints": {
                endpoint_name: {
                    "success_count": data["success_count"],
                    "failure_count": data["failure_count"],
                    "success_rate": (data["success_count"] / (data["success_count"] + data["failure_count"])) * 100 
                    if (data["success_count"] + data["failure_count"]) > 0 else 0,
                    "avg_response_time": sum(data["response_times"]) / len(data["response_times"]) 
                    if data["response_times"] else 0,
                    "min_response_time": min(data["response_times"]) if data["response_times"] else 0,
                    "max_response_time": max(data["response_times"]) if data["response_times"] else 0,
                    "p95_response_time": sorted(data["response_times"])[int(len(data["response_times"]) * 0.95)] 
                    if data["response_times"] and len(data["response_times"]) > 1 else 0
                }
                for endpoint_name, data in results.items()
            }
        }, f, indent=2)
    print(f"{Fore.CYAN}Saved detailed results to {json_filename}{Style.RESET_ALL}")

def main():
    parser = argparse.ArgumentParser(description="Load test for BEL MES API")
    parser.add_argument("--url", default="http://localhost:32000", help="Base URL of the API")
    parser.add_argument("--requests", type=int, default=100, help="Number of requests per endpoint")
    parser.add_argument("--concurrency", type=int, default=10, help="Number of concurrent requests")
    args = parser.parse_args()
    
    # Define your endpoints for testing
    # You should customize these based on your actual API endpoints
    endpoints = [
        {"name": "Root Endpoint", "path": "/", "method": "GET"},
        {"name": "Health Check", "path": "/health", "method": "GET"},
        # Add your specific endpoints here
        # For example:
        # {"name": "Get Users", "path": "/api/users", "method": "GET"},
        # {"name": "Create User", "path": "/api/users", "method": "POST", "data": {"name": "Test User", "email": "test@example.com"}}
    ]
    
    # Auth endpoint (if needed)
    auth_endpoint = {
        "path": "/token",
        "method": "POST"
    }
    
    auth_data = {
        "username": "admin",
        "password": "password"
    }
    
    asyncio.run(run_load_test(
        args.url, 
        args.requests, 
        args.concurrency, 
        endpoints,
        auth_endpoint=None,  # Set to auth_endpoint if auth is needed
        auth_data=None       # Set to auth_data if auth is needed
    ))

if __name__ == "__main__":
    main()
