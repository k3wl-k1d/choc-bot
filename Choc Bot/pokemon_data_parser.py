numPokemon = 1025
file = "pokemon_stats.txt"
statMap = dict([(0, "HP"), (1, "ATTACK"), (2, "DEFENSE"), (3, "SPECIAL ATTACK"), (4, "SPECIAL DEFENSE"), (5, "SPEED")])

def parse_stats(fileName : str, numPokemon : int) -> dict:
    pokemonStats = dict()
    try:
        reader = open(fileName, "r")
    except FileNotFoundError:
        print("Whoops! File does not exist, bozo!")
        return None
    
    for i in range(numPokemon):
        try:
            key = reader.readline().strip()

            # Create empty key in dictionary given name
            pokemonStats[key] = 0

            # Create list of stats
            pokemonStatsList = []
            for j in range(6):
                pokemonStatsList.append(reader.readline().strip())

            pokemonStats[key] = pokemonStatsList
        except:
            print(f"Error at line {i + 1}")
    
    return pokemonStats

def parse_names(fileName : str, numPokemon : int) -> list:
    pokemonNames = []

    try:
        reader = open(fileName, "r")
    except FileNotFoundError:
        print("Whoops! File does not exist, bozo!")
        return None
    
    for i in range(numPokemon):
        try:
            pokemonNames.append(reader.readline().strip())
            for j in range(6):
                reader.readline()
        except:
            print(f"Error at line {i + 1}")
    
    return pokemonNames


if __name__ == '__main__':
    finalDict = parse_stats(file, 1025)
    print(finalDict)