import bcrypt
print(bcrypt.hashpw(b'brewers123', bcrypt.gensalt()).decode())