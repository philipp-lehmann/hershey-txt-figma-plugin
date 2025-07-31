import os
import re
import xml.etree.ElementTree as ET

FONTS_DIR = '../fonts'
OUTPUT_DIR = '../fontsdata'

os.makedirs(OUTPUT_DIR, exist_ok=True)

def parse_path(d):
    path = []
    # Split the path into commands and their associated arguments
    commands = re.findall(r'[MLZmlz][^MLZmlz]*', d)
    
    for cmd in commands:
        code = cmd[0]  # Command (M, L, C, Z, etc.)
        args = list(map(float, re.findall(r'[-+]?[0-9]*\.?[0-9]+', cmd[1:])))
        
        # Handle the 'M' (Move to) command
        if code.upper() == 'M':
            path.append(None)  # Indicating a move
            for i in range(0, len(args), 2):
                # Ensure that args[i+1] exists
                if i + 1 < len(args):
                    path.append([args[i], args[i + 1]])
                else:
                    print(f"Warning: Missing argument for 'M' command at index {i}. Skipping.")

        # Handle the 'L' (Line to) command
        elif code.upper() == 'L':
            for i in range(0, len(args), 2):
                # Ensure that args[i+1] exists
                if i + 1 < len(args):
                    path.append([args[i], args[i + 1]])
                else:
                    print(f"Warning: Missing argument for 'L' command at index {i}. Skipping.")

        # Handle the 'C' (Cubic Bezier curve) command
        elif code.upper() == 'C':
            for i in range(0, len(args), 6):
                if i + 5 < len(args):
                    control1 = [args[i], args[i + 1]]
                    control2 = [args[i + 2], args[i + 3]]
                    endpoint = [args[i + 4], args[i + 5]]
                    path.append(['C', control1, control2, endpoint])  # Store as cubic curve
                else:
                    print(f"Warning: Incomplete arguments for 'C' command at index {i}. Skipping.")

        # Handle the 'Z' (Close path) command
        elif code.upper() == 'Z':
            path.append(None)  # Indicating the close path

    return path

def parse_svg_font(file_path):
    tree = ET.parse(file_path)
    root = tree.getroot()
    ns = {'svg': 'http://www.w3.org/2000/svg'}

    font = root.find('.//svg:font', ns)
    if font is None:
        return None

    font_horiz_adv_x = float(font.attrib.get('horiz-adv-x', '0'))
    glyphs = font.findall('svg:glyph', ns)

    glyph_data = {}

    for glyph in glyphs:
        unicode_char = glyph.attrib.get('unicode')
        d = glyph.attrib.get('d')
        if not unicode_char or not d or not unicode_char.isprintable():
            continue
        
        path = parse_path(d)
        horiz_adv_x = float(glyph.attrib.get('horiz-adv-x', font_horiz_adv_x))
        
        # Handle special characters (like space, quotes) with proper encoding
        char = unicode_char.replace('\\', '\\\\').replace("'", "\\'")

        # Assign path and horiz_adv_x to each character in glyph_data
        glyph_data[char] = {
            'paths': path,
            'horiz_adv_x': horiz_adv_x
        }

    return {'glyphs': glyph_data}



def format_as_ts_object(data, var_name):
    glyphs = data['glyphs']

    lines = [f"const {var_name}: {{ glyphs: {{ [key: string]: {{ paths: Array<[number, number] | null>, horiz_adv_x: number }} }} }} = {{"]
    lines.append("  glyphs: {")

    for char, glyph in glyphs.items():
        path_str = ', '.join(
            'null' if pt is None else f'[{pt[0]:.4f}, {pt[1]:.4f}]'
            for pt in glyph['paths']
        )
        lines.append(f"    '{char}': {{")
        lines.append(f"      paths: [{path_str}],")
        lines.append(f"      horiz_adv_x: {glyph['horiz_adv_x']}")
        lines.append(f"    }},")

    lines.append("  }")
    lines.append("};")
    return '\n'.join(lines)


def filename_to_varname(filename):
    base = os.path.splitext(os.path.basename(filename))[0]
    return ''.join(c if c.isalnum() else '_' for c in base).upper() + '_FONT_DATA'

def process_all_fonts():
    all_ts_content = []  # List to accumulate all the TS content
    
    for fname in os.listdir(FONTS_DIR):
        if fname.endswith('.svg'):
            input_path = os.path.join(FONTS_DIR, fname)
            font_data = parse_svg_font(input_path)
            if not font_data:
                print(f"Skipped: {fname} (no valid font)")
                continue
            var_name = filename_to_varname(fname)
            ts_content = format_as_ts_object(font_data, var_name)
            all_ts_content.append(ts_content)  # Accumulate the TS content
            
            print(f"Converted: {fname}")
    
    # Write all the accumulated content into one single file
    if all_ts_content:
        combined_ts_content = "\n\n".join(all_ts_content)  # Join the content with two newlines between each font
        output_path = os.path.join(OUTPUT_DIR, 'combined_fonts.ts')
        with open(output_path, 'w') as f:
            f.write(combined_ts_content)
        print(f"All fonts converted and saved to {output_path}")
    else:
        print("No valid fonts to process.")

if __name__ == '__main__':
    process_all_fonts()
