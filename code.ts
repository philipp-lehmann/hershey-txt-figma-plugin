// This file contains the main logic for the Figma plugin.
// It listens for messages from the UI and performs actions on the Figma canvas.

// Define a simplified Hershey font data structure.
// Each character is an array of coordinate pairs (x, y).
// A null value indicates a "pen up" command, starting a new stroke.
// The coordinates are relative and will be scaled and positioned.
// This is a *very limited* set for demonstration. A full Hershey font
// implementation would require a much larger data set or a parsing library.

import { HERSHEY_FONT_DATA } from './hershey_data';

// Show the plugin UI using the __html__ global variable (populated by build process)
figma.showUI(__html__);
figma.ui.resize(500, 400);

// Listen for messages from the UI
figma.ui.onmessage = async (msg) => {
    if (msg.type === 'convert-text') {
        const { fontSize, lineWidth, lineHeight, textStyle } = msg;

        // Ensure only one text node is selected
        if (figma.currentPage.selection.length !== 1 || figma.currentPage.selection[0].type !== 'TEXT') {
            figma.notify('Please select exactly one text layer to convert.', { timeout: 3000 });
            figma.ui.postMessage({ type: 'close' }); // Close UI if selection is invalid
            return;
        }

        const selectedTextNode = figma.currentPage.selection[0] as TextNode;
        // Split text content by newlines to handle explicit line breaks
        const lines = selectedTextNode.characters.toUpperCase().split('\n');
        const originalFill = selectedTextNode.fills; // Get original fill color for stroke

        // Calculate scaling factor based on desired font size and a base character height (e.g., 10 units in HERSHEY_FONT_DATA)
        const scale = fontSize / 10;
        const characterSpacing = 2 * scale; // Add some spacing between characters
        const lineHeightSpacing = fontSize * lineHeight; // Spacing between lines, based on font size

        const allVectorPaths: VectorPath[] = [];
        let currentYOffset = 0; // Vertical offset for each line

        // Process each line of text
        for (let i = 0; i < lines.length; i++) {
            const lineContent = lines[i];
            let currentXOffset = 0; // Horizontal offset for characters within the current line

            // Generate paths for each character in the current line
            for (const char of lineContent) {
                const charData = HERSHEY_FONT_DATA[char] || []; // Get data for the character, or empty array if not found

                if (charData.length === 0 && char !== ' ') {
                    // If character data is missing and it's not a space, notify and skip.
                    console.warn(`Hershey data not found for character: ${char}. Skipping.`);
                    currentXOffset += (10 * scale) + characterSpacing; // Advance X for missing char
                    continue;
                }

                let pathData = "";
                let penDown = false;
                let charMinX = Infinity; // Track min X relative to character's own coordinate system
                let charMaxX = -Infinity; // Track max X relative to character's own coordinate system

                // Convert Hershey coordinates to SVG path commands
                for (const segment of charData) {
                    if (segment === null) {
                        penDown = false; // Pen up, start new subpath
                    } else {
                        const [x, y] = segment;
                        // Track min/max X relative to character's own coordinate system
                        charMinX = Math.min(charMinX, x);
                        charMaxX = Math.max(charMaxX, x);

                        const transformedX = x * scale + currentXOffset;
                        // Apply currentYOffset for line positioning
                        const transformedY = y * scale + currentYOffset;

                        if (!penDown) {
                            pathData += `M ${transformedX} ${transformedY} `;
                            penDown = true;
                        } else {
                            pathData += `L ${transformedX} ${transformedY} `;
                        }
                    }
                }

                if (pathData) {
                    allVectorPaths.push({
                        windingRule: 'NONZERO', // Standard winding rule
                        data: pathData.trim()
                    });
                }

                // Advance X offset for the next character
                // Calculate character width based on its relative coordinates, then scale.
                const charWidth = (charMaxX - charMinX);
                // If charWidth is 0 (e.g., for some punctuation or if charData is empty),
                // provide a default width for spacing.
                const effectiveCharWidth = charWidth > 0 ? charWidth * scale : (10 * scale); // Default width if character has no horizontal extent

                currentXOffset += effectiveCharWidth + characterSpacing;
            }
            // After processing a line, advance the Y offset for the next line
            currentYOffset += lineHeightSpacing;
        }


        // Create a new VectorNode
        const vectorNode = figma.createVector();
        vectorNode.name = `${selectedTextNode.name} (Hershey)`;
        vectorNode.vectorPaths = allVectorPaths;

        // Set stroke properties
        vectorNode.strokeWeight = lineWidth;
        // Use the original text's fill color as the stroke color for the new vector
        if (Array.isArray(originalFill) && originalFill[0] && originalFill[0].type === 'SOLID') {
            vectorNode.strokes = [{
                type: 'SOLID',
                color: originalFill[0].color,
                opacity: originalFill[0].opacity || 1,
            }];
        } else {
            // Default stroke if original fill is not a solid color
            vectorNode.strokes = [{ type: 'SOLID', color: { r: 0, g: 0, b: 0 } }]; // Black
        }

        // Position the new vector node relative to the original text node
        vectorNode.x = selectedTextNode.x;
        vectorNode.y = selectedTextNode.y;

        // Add the new vector node to the current page
        figma.currentPage.appendChild(vectorNode);

        // Select the newly created vector node
        figma.currentPage.selection = [vectorNode];

        // Zoom to fit the new node (optional, but helpful for user)
        figma.viewport.scrollAndZoomIntoView([vectorNode]);

        figma.notify('Text converted to Hershey vector paths!', { timeout: 2000 });
        figma.ui.postMessage({ type: 'close' }); // Close the UI after conversion

    } else if (msg.type === 'cancel') {
        // Close the plugin UI if the cancel button is clicked
        figma.ui.postMessage({ type: 'close' });
    } else if (msg.type === 'close') {
        // When the UI sends a 'close' message, close the plugin
        figma.closePlugin();
    }
};
