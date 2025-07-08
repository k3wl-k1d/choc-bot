import requests
API_URL = "https://pokeapi.co/api/v2/"
numPokemon = 1025

# Set False when should not write
writeMode = True

def pokemon_get_info(pokedex):
    url = f"{API_URL}/pokemon/{pokedex}"
    response = requests.get(url)
    
    # If the connection is OK (200)
    if response.status_code == 200:
        return response.json()
    # If not an OK connection
    else:
        print(f"Failed to retrieve data : {response.status_code}")
        return

if (__name__ == '__main__'):
    info = pokemon_get_info(25)

    if info:
        print(f"NAME: {info["name"]}")
        print(f"HP: {info["stats"][0]["base_stat"]}")
        print(f"ATK: {info["stats"][1]["base_stat"]}")
        print(f"DEF: {info["stats"][2]["base_stat"]}")
        print(f"SPA: {info["stats"][3]["base_stat"]}")
        print(f"SPD: {info["stats"][4]["base_stat"]}")
        print(f"SPE: {info["stats"][5]["base_stat"]}")
    
    if (writeMode):
        # Open file to write
        writer = open("pokemon_stats.txt", "w")

        for i in range(1, numPokemon+1):
            info = pokemon_get_info(i)

            print(f"Writing Pokemon #{i}, NAME : {info["name"]}\n")
            writer.write(f"{info["name"]}\n{info["stats"][0]["base_stat"]}\n{info["stats"][1]["base_stat"]}\n{info["stats"][2]["base_stat"]}\n{info["stats"][3]["base_stat"]}\n{info["stats"][4]["base_stat"]}\n{info["stats"][5]["base_stat"]}\n")