import discord
import random
import string
import responses as resp
import reactions as reac
import replies as repl
import battlefactory_info as bf
import pokemon_data_parser as p_data_parser


async def battlefactory(requester: discord.User, opponent: discord.User):
    print("HELLO!!!")
    # Get the two participants
    challenger = requester.author
    challenged = opponent
    battle = random.choice(bf.BATTLEFACTORY_DATA)
    
    coin = random.randint(0,1)

    if coin:
        home = battle[0]
        away = battle[1]
    else:
        away = battle[0]
        home = battle[1]
    
    msg = (
        f"**WELCOME TO THE BATTLE FACTORY**\n\n"
        f"Fight for my amusement, *pigs*\n"
        f"**{home[0]}**: *{home[1]}* vs *{away[1]}*\n\n"
    )

    teamHome = f"You will be playing as {home[1]}.\nYour captains are: {home[3]}\n{home[2]}\n"
    teamAway = f"You will be playing as {away[1]}.\nYour captains are: {away[3]}\n{away[2]}\n"

    # DM both users
    try:
        await challenger.send(f"Hey, {challenger.name} you bitch. You’re challenging {challenged.name}!\n\n{msg}{teamHome}")
    except discord.Forbidden:
        await requester.send(f"⚠️ I couldn’t fucking DM {challenger.mention}. Please enable DMs or shut the hell up!")

    try:
        await challenged.send(f"Hey, {challenged.name} you bitch! {challenger.name} has challenged you!\n\n{msg}{teamAway}")
    except discord.Forbidden:
        await requester.send(f"⚠️ I couldn’t fucking DM {challenger.mention}. Please enable DMs or shut the hell up!")

    # Optionally confirm in the channel
    await requester.send(f"Sent Battle Factory instructions to {challenger.mention} and {challenged.mention}! BL do not HF!")