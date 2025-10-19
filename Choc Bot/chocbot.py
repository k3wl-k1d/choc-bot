import discord
import random
import os
import string
import responses as resp
import reactions as reac
import replies as repl
import battlefactory_info as bf
import pokemon_data_parser as p_data_parser

# Load bot token from an environment variable
KEY = "CHOC_DISCORD_BOT_KEY"
TOKEN = os.getenv(KEY)

# Dev script
DEV = 513462153093840896

# Get a list of all Pokemon names
allPokemonNames = p_data_parser.parse_names(p_data_parser.file, p_data_parser.numPokemon)

# Get stat dictionary and stat map from parser - BOTH DICTIONARIES
allPokemonStats = p_data_parser.parse_stats(p_data_parser.file, p_data_parser.numPokemon)
pokemonStatMap = p_data_parser.statMap

# Create bot instance
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # needed to resolve user mentions

# Initialize bot client
client = discord.Client(intents=intents)

currentQuestion = None
answerString = None
failedAttempts = 0
stat = 0
pokemon = 0
attempts = 5
msgSent = False

@client.event
async def on_ready():
    print(f'Logged in as {client.user}')

@client.event
async def on_message(message):    
    global currentQuestion, answerString, pokemon, stat, failedAttempts, msgSent

    if message.author == client.user:
        return  # Ignore its own messages

    if client.user.mentioned_in(message):
        msgSent = True
        print(f'Ping received in message : {message.content}')
        response = random.choice(resp.RESPONSES)
        await message.channel.send(response)

    msgContent = message.content.lower()

    try:
        # Code for action
        if msgContent[0] == '!':
            msgSent = True

            if msgContent == "!help":
                await message.channel.send(f"Current commands:\n-**!stat**: Quick stat quiz\n-**!stats** or **!s**: Multi-round stats quiz\n"
                    "-**!battlefactory** @opp: Sends you and your opponent a BattleFactory challenge\n"
                    "-**!battlefactoryf** [League], [0, 1, or 2 (ALL, SINGLES, SPECIAL formats)], @opp: Sends you and your opponent a BattleFactory challenge based on the filters\n"
                    "-**!stop**: Stops the current action if busy")
                

            # Display a quick stat quiz
            if msgContent == "!stat":
                pkTemp = random.randint(1,p_data_parser.numPokemon)
                stTemp = random.randint(0,5)
                trainingQuestion = f"What is *{string.capwords(allPokemonNames[pkTemp], sep='-')}*'s {pokemonStatMap.get(stTemp)} stat?\n\tANSWER: ||{allPokemonStats.get(allPokemonNames[pkTemp])[stTemp]}||"

                print(f'Stat training asked in message : {message.content}\nAnswer : {allPokemonStats.get(allPokemonNames[pkTemp])[stTemp]}')
                await message.channel.send(trainingQuestion)

            # Display a full stat quiz
            if msgContent == "!stats" or msgContent == "!s":
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
                currentQuestion = f"What is *{string.capwords(allPokemonNames[pokemon], sep='-')}*'s {pokemonStatMap.get(stat)} stat? Use the keyword '!answer' before you respond.\n\n"
                print(f'\nStat game asked in message : {message.content}\nAnswer : {allPokemonStats.get(allPokemonNames[pokemon])[stat]}\n')
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
        
            # Battle factory code
            if msgContent[0:14] == "!battlefactory":
                print(f"Battle factory requested : {message.content}")
                # Get the two participants
                challenger = message.author
                challenged = message.mentions[0]
                battle = None

                # Check for filters
                if msgContent[14:15] == 'f':
                    print(f"Battle factory filter")
                    filters = msgContent[15:].replace(' ', '').split(',')
                    leagueFilter = filters[0].upper()
                    formatFilter = int(filters[1])  # 0=all, 1=standard only, 2=special only

                    # Filter possible battles
                    validBattles = []
                    for battle in bf.BATTLEFACTORY_DATA:
                        league_name = bf.LEAGUE_INDEX.get(battle[0], "").upper()
                        isStandard = battle[1]

                        # League must match
                        if league_name != leagueFilter:
                            continue

                        # Format filter
                        if formatFilter == 1 and not isStandard:
                            continue
                        elif formatFilter == 2 and isStandard:
                            continue

                        validBattles.append(battle)

                    # Pick random from filtered
                    if validBattles:
                        battle = random.choice(validBattles)
                    else:
                        # No matches found
                        print("No battles matched your filters.")
                else:
                    print(f"Battle factory standard")
                    battle = random.choice(bf.BATTLEFACTORY_DATA)

                if battle:
                    coin = random.randint(0,1)

                    # Randomize order
                    if coin:
                        home = battle[4]
                        away = battle[5]
                    else:
                        away = battle[4]
                        home = battle[5]
                    
                    msg = (
                        f"\n**WELCOME TO THE BATTLE FACTORY**\n\n"
                        f"Fight for my amusement, *pigs*\n"
                        f"**{bf.LEAGUE_INDEX.get(battle[0])} {battle[2]}**: *{home[0]}* vs *{away[0]}*\n\n"
                    )

                    teamHome = f"You will be playing as {home[0]}.\nYour captains are: {home[2]}\n{home[1]}\n"
                    teamAway = f"You will be playing as {away[0]}.\nYour captains are: {away[2]}\n{away[1]}\n"

                    # DM both users
                    try:
                        await challenger.send(f"Hey, {challenger.name}! You’re challenging {challenged.name}!\n\n{msg}{teamHome}\n")
                    except discord.Forbidden:
                        await message.channel.send(f"⚠️ I couldn’t fucking DM {challenger.mention}. Please enable DMs or shut the hell up!")

                    try:
                        await challenged.send(f"Hey, {challenged.name}! {challenger.name} has challenged you!\n\n{msg}{teamAway}\n")
                    except discord.Forbidden:
                        await message.channel.send(f"⚠️ I couldn’t fucking DM {challenger.mention}. Please enable DMs or shut the hell up!")

                    # Optionally confirm in the channel
                    await message.channel.send(f"Sent Battle Factory instructions to {challenger.mention} and {challenged.mention}!\n**{bf.LEAGUE_INDEX.get(battle[0])} {battle[2]}, {battle[3]}**: *{home[0]}* vs *{away[0]}*. BL do not HF!")
                
                else:
                    await message.channel.send(f"Something may have gone wrong... Did you make a typo?")
            
            # Kill switch
            if msgContent == "!kill Choc" and message.author.id == DEV:
                print(f"Killing Choc-bot. Requested by {message.author.id}")
                await message.channel.send("WHAT?! NOOOOOOO!!!")
                
                exit()
        
    except:
        print("Error : Index out of range for some reason: ", msgContent)

    if not msgSent:
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
    else:
        msgSent = False

# Run bot
client.run(TOKEN)