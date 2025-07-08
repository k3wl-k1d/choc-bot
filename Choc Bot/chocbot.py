import discord
import random
import os
import string
import responses as resp
import reactions as reac
import replies as repl
import pokemon_data_parser as p_data_parser

# Load bot token from an environment variable
KEY = "CHOC_DISCORD_BOT_KEY"
TOKEN = os.getenv(KEY)

# Get a list of all Pokemon names
allPokemonNames = p_data_parser.parse_names(p_data_parser.file, p_data_parser.numPokemon)

# Get stat dictionary and stat map from parser - BOTH DICTIONARIES
allPokemonStats = p_data_parser.parse_stats(p_data_parser.file, p_data_parser.numPokemon)
pokemonStatMap = p_data_parser.statMap

# Enable message content intent
intents = discord.Intents.default()
intents.message_content = True

# Initialize bot client
client = discord.Client(intents=intents)

currentQuestion = None
answerString = None
failedAttempts = 0
stat = 0
pokemon = 0
attempts = 5

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message):    
    global currentQuestion, answerString, pokemon, stat, failedAttempts

    if message.author == client.user:
        return  # Ignore its own messages

    if client.user.mentioned_in(message):
        print(f'Ping received in message : {message.content}')
        response = random.choice(resp.RESPONSES)
        await message.channel.send(response)

    msgContent = message.content.lower()

    try:
        if msgContent[0] == '!':
            if msgContent == "!stat" or msgContent == "!s":
                pkTemp = random.randint(1,p_data_parser.numPokemon)
                stTemp = random.randint(0,5)
                trainingQuestion = f"What is *{string.capwords(allPokemonNames[pkTemp], sep='-')}*'s {pokemonStatMap.get(stTemp)} stat?\n\tANSWER: ||{allPokemonStats.get(allPokemonNames[pkTemp])[stTemp]}||"

                print(f'Stat training asked in message : {message.content}\nAnswer : {allPokemonStats.get(allPokemonNames[pkTemp])[stTemp]}')
                await message.channel.send(trainingQuestion)

            if msgContent == "!stats":
                # Becomes busy when a question is asked
                if currentQuestion:
                    await message.channel.send("**My schedule's too busy right now...**")
                    return
                
                # Continues if no question in queue
                pokemon = random.randint(1,p_data_parser.numPokemon)
                stat = random.randint(0,5)

                # Ask question
                failedAttempts = 0
                answerString = f"*{string.capwords(allPokemonNames[pokemon], sep='-')}*'s {pokemonStatMap.get(stat)} stat is {allPokemonStats.get(allPokemonNames[pokemon])[stat]}"
                currentQuestion = f"What is *{string.capwords(allPokemonNames[pokemon], sep='-')}*'s {pokemonStatMap.get(stat)} stat?\n\n"
                print(f'Stat game asked in message : {message.content}\nAnswer : {allPokemonStats.get(allPokemonNames[pokemon])[stat]}')
                await message.channel.send(currentQuestion)

            elif msgContent[1:] == 'stop':
                    await message.channel.send(f"Y'all lose... {answerString}")
                    failedAttempts = 0
                    currentQuestion = None
                    answerString = None

            # Congratulates winner if right answer is typed given command
            elif currentQuestion and msgContent[1:8] == "answer ":
                # Correct answer
                if msgContent[8:] == allPokemonStats.get(allPokemonNames[pokemon])[stat]:
                    await message.channel.send(f"You're a weiner, {message.author.mention}! {answerString}")
                    failedAttempts = 0
                    currentQuestion = None
                    answerString = None
                # Force stop
                else:
                    await message.channel.send(f"**WRONG** {message.author.mention}! {currentQuestion}")
                    # await message.add_reaction("❌")
                    failedAttempts += 1
                    # If too many failed attempts, stop
                    if failedAttempts == attempts:
                        await message.channel.send(f"Y'all lose... {answerString}")
                        failedAttempts = 0
                        currentQuestion = None
                        answerString = None
    except:
        print("Error : Index out of range for some reason")

    for keyword, reply in repl.REPLIES.items():
        if keyword in msgContent:
            print(f'Reply keyword seen in message : {message.content}')
            await message.channel.send(reply)
            break
    
    for keyword, emoji in reac.REACTIONS.items():
        if keyword in msgContent:  # Check if the keyword is in the message
            print(f'Reaction keyword seen in message : {message.content}')
            await message.add_reaction(emoji)  # Add reaction to the message
            break  # Stop checking after the first match

# Run bot
client.run(TOKEN)