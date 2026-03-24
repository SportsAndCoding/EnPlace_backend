import bcrypt
print(bcrypt.hashpw(b'Brewers2025!', bcrypt.gensalt()).decode())