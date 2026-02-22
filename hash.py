import bcrypt
print(bcrypt.hashpw(b'honeygurl92!!', bcrypt.gensalt()).decode())