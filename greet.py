def greet(name):
    """Greet a person by name.
    
    Args:
        name: The name of the person to greet.
    
    Returns:
        A greeting string in the format 'Hello, {name}!'
    """
    return f"Hello, {name}!"

if __name__ == "__main__":
    print(greet("World"))
